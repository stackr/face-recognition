"""Bounded snapshots leave the GPU scheduler; persistence and HTTP run here."""

import logging
import threading
from collections import Counter
from queue import Empty, Full, Queue

import httpx

from app.db.session import make_engine
from app.services.events import Candidate, EventStore


class EventWriter:
    def __init__(self, settings, engine=None, transport=None):
        self.settings = settings
        self.engine = engine if engine is not None else make_engine(settings)
        self.owned = engine is None
        self.store = EventStore(settings, self.engine)
        self.queue = Queue(maxsize=settings.event_queue_size)
        self.cancel = threading.Event()
        self.stats = Counter()
        self.lock = threading.Lock()
        self.client = httpx.Client(
            base_url=settings.api_url,
            timeout=2,
            trust_env=False,
            headers={"X-Service-Token": settings.service_token.get_secret_value()},
            transport=transport,
        )
        self.thread = threading.Thread(target=self.run, daemon=True, name="event-writer")
        self.thread.start()

    def count(self, name):
        with self.lock:
            self.stats[name] += 1

    def status(self):
        with self.lock:
            return dict(self.stats) | {
                "pending": self.queue.qsize(),
                "queue_limit": self.queue.maxsize,
            }

    def submit(self, candidate):
        if self.cancel.is_set():
            return False
        try:
            self.queue.put_nowait(candidate)
            return True
        except Full:
            self.count("queue_full")
            return False

    def submit_tracks(self, camera_id, session, faces, tracks, revision, now):
        with faces.lock:
            for track in tracks:
                state = faces.tracks.get(track["track_id"])
                best = state["best"] if state else None
                if not best or best["stream_session_id"] != session:
                    continue
                attempts = state.setdefault("event_attempts", {})
                matches = track.get("face", {}).get("matches", [])
                identifiers = {match["person_id"] for match in matches}
                for key in set(attempts) - identifiers:
                    del attempts[key]
                for match in matches:
                    person_id = match["person_id"]
                    evidence = state.get("match_samples", {}).get(person_id, best)
                    if now - attempts.get(person_id, float("-inf")) < max(
                        0.5, self.settings.event_cooldown
                    ):
                        continue
                    candidate = Candidate(
                        camera_id,
                        session,
                        track["track_id"],
                        person_id,
                        match["face_id"],
                        revision,
                        evidence["captured_at"],
                        match["similarity"],
                        evidence["quality"],
                        evidence["jpeg"],
                        evidence["frame_jpeg"],
                    )
                    if self.submit(candidate):
                        attempts[person_id] = now

    def persist(self, candidate):
        for attempt in range(3):
            if self.cancel.is_set():
                return None
            try:
                event_id = self.store.record(candidate)
                self.count("saved" if event_id is not None else "coalesced_or_ineligible")
                return event_id
            except Exception as exc:
                self.count("save_failures")
                logging.getLogger("cctv.events").warning(
                    "Event save unavailable; type=%s", type(exc).__name__
                )
                if attempt < 2:
                    self.cancel.wait(0.25 * 2**attempt)
        return None

    def notify(self, event_id):
        for attempt in range(3):
            if self.cancel.is_set():
                return
            try:
                self.client.post(
                    "/internal/events/notify", json={"event_id": event_id}
                ).raise_for_status()
                return
            except httpx.HTTPError:
                self.count("notification_failures")
                if attempt < 2:
                    self.cancel.wait(0.25 * 2**attempt)
        # API tails the journal too; a lost wakeup cannot lose persisted history.

    def run(self):
        while not self.cancel.is_set():
            try:
                candidate = self.queue.get(timeout=0.2)
            except Empty:
                continue
            try:
                event_id = self.persist(candidate)
                if event_id is not None:
                    self.notify(event_id)
            finally:
                self.queue.task_done()

    def close(self):
        self.cancel.set()
        self.thread.join(timeout=15)
        if not self.thread.is_alive():
            self.client.close()
            if self.owned:
                self.engine.dispose()
