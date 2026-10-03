"""Bounded nonblocking diagnostics; DB work stays off the GPU scheduler."""

import logging
import threading
import time
from collections import Counter
from queue import Empty, Full, Queue

from sqlalchemy.orm import Session

from app.services.recognition import RecognitionStore, load_sampling, observation


class DiagnosticsWriter:
    def __init__(self, settings, engine, runtime):
        self.settings, self.engine, self.runtime = settings, engine, runtime
        self.store = RecognitionStore(settings, engine)
        self.queue = Queue(maxsize=settings.recognition_log_queue_size)
        self.cancel = threading.Event()
        self.lock = threading.Lock()
        self.stats = Counter()
        self.thread = threading.Thread(target=self.run, daemon=True, name="recognition-logs")
        self.thread.start()

    def count(self, name, amount=1):
        with self.lock:
            self.stats[name] += amount

    def status(self):
        with self.lock:
            return dict(self.stats) | {
                "pending": self.queue.qsize(),
                "queue_limit": self.queue.maxsize,
            }

    def submit_tracks(self, camera_id, session, frame, tracks, revision, search_status):
        for track in tracks:
            item = observation(
                camera_id,
                session,
                frame.frame_id,
                frame.captured_at,
                track,
                revision,
                search_status,
            )
            if item is None or self.cancel.is_set():
                continue
            try:
                self.queue.put_nowait(item)
            except Full:
                self.count("dropped")

    def reload(self):
        with Session(self.engine) as db:
            revision, values = load_sampling(db, self.settings)
        self.runtime.queue_sampling(revision, values)
        return revision

    def run(self):
        next_poll = 0
        while not self.cancel.is_set() or not self.queue.empty():
            if not self.cancel.is_set() and time.monotonic() >= next_poll:
                try:
                    self.reload()
                except Exception as exc:
                    self.count("settings_failures")
                    logging.getLogger("cctv.recognition").warning(
                        "Settings reload failed; type=%s", type(exc).__name__
                    )
                next_poll = time.monotonic() + 2
            batch = []
            try:
                batch.append(self.queue.get(timeout=0.2))
            except Empty:
                continue
            while len(batch) < 64:
                try:
                    batch.append(self.queue.get_nowait())
                except Empty:
                    break
            for attempt in range(3):
                try:
                    self.store.save(batch)
                    self.count("saved", len(batch))
                    break
                except Exception as exc:
                    self.count("save_failures")
                    if attempt == 2:
                        self.count("dropped", len(batch))
                        logging.getLogger("cctv.recognition").warning(
                            "Diagnostics save failed; type=%s", type(exc).__name__
                        )
                    else:
                        self.cancel.wait(0.25 * 2**attempt)
            for _ in batch:
                self.queue.task_done()

    def close(self):
        self.cancel.set()
        self.thread.join(timeout=15)
