"""Private compressed circular segments; capture/GPU never wait for clip encoding."""

import os
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import cv2
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MatchEvent
from app.models.foundation import utc_now


@dataclass(frozen=True)
class StoredFrame:
    path: Path
    offset: int
    size: int
    timestamp: float


class Recording:
    def __init__(self, directory):
        self.directory = directory
        directory.mkdir(mode=0o700)
        self.frames = []
        self.pending = None
        self.closed = False
        self.encoding = False
        self.error = None
        self.last_offer = -float("inf")
        self.bytes = self.dropped = self.pins = 0
        self.segment = None
        self.segment_second = None
        self.created = time.monotonic()

    def trim(self, cutoff):
        if self.pins:
            return
        # Segments contain at most one second. Keep the active segment intact.
        removable = {f.path for f in self.frames if f.timestamp < cutoff}
        removable -= {f.path for f in self.frames if f.timestamp >= cutoff}
        removable.discard(self.segment)
        self.frames = [f for f in self.frames if f.path not in removable]
        for path in removable:
            self.bytes -= path.stat().st_size
            path.unlink(missing_ok=True)


class ClipManager:
    def __init__(self, settings, store, *, start=True):
        self.settings, self.store = settings, store
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.recordings = {}
        self.completed = self.failed = 0
        self.last_cleanup = 0
        self.reserved_bytes = 0
        for directory in (settings.clip_dir, settings.clip_buffer_dir):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(directory, 0o700)
        self.capture_thread = threading.Thread(
            target=self.encode_frames, daemon=True, name="clip-buffer"
        )
        self.clip_thread = threading.Thread(
            target=self.process_events, daemon=True, name="clip-encode"
        )
        if start:
            # No recorder survives a worker restart. Persist the failure for recovery.
            with Session(store.engine) as db:
                pending = list(
                    db.scalars(select(MatchEvent.id).where(MatchEvent.clip_state == "pending"))
                )
            for event_id in pending:
                store.finish_clip(event_id, error="worker_restarted")
            # Recover only manager-owned UUID directories from a previous process.
            import re

            for path in settings.clip_buffer_dir.iterdir():
                if (
                    re.fullmatch(r"[a-f0-9]{32}", path.name)
                    and path.is_dir()
                    and not path.is_symlink()
                ):
                    shutil.rmtree(path)
            self.capture_thread.start()
            self.clip_thread.start()

    def offer(self, camera_id, frame):
        if self.cancel.is_set():
            return
        key = (camera_id, frame.stream_session_id)
        if not self.lock.acquire(blocking=False):
            return
        try:
            recording = self.recordings.get(key)
            if recording is None:
                # Limit retained sessions, including fast looping MP4 files.
                if len(self.recordings) >= self.settings.max_active_cameras * 8:
                    return
                recording = self.recordings[key] = Recording(
                    self.settings.clip_buffer_dir / uuid.uuid4().hex
                )
            if (
                recording.closed
                or frame.captured_mono - recording.last_offer < 1 / self.settings.clip_fps
            ):
                return
            recording.last_offer = frame.captured_mono
            recording.dropped += int(recording.pending is not None)
            recording.pending = frame
        except Exception:
            # Capture continues even when the filesystem cannot create a buffer.
            self.failed += 1
        finally:
            self.lock.release()

    def end_session(self, camera_id, session_id):
        with self.lock:
            recording = self.recordings.get((camera_id, session_id))
            if recording:
                recording.closed = True

    def storage_available(self, extra=0):
        directories = (self.settings.clip_dir, self.settings.clip_buffer_dir)
        used = sum(
            p.stat().st_size
            for d in directories
            for p in d.rglob("*")
            if p.is_file() and not p.is_symlink()
        )
        return (
            used + extra + self.reserved_bytes <= self.settings.storage_max_bytes
            and shutil.disk_usage(self.settings.clip_dir).free - extra - self.reserved_bytes
            >= self.settings.storage_min_free_bytes
        )

    def append(self, recording, frame):
        image = frame.image
        width = min(image.shape[1], self.settings.clip_width)
        height = max(2, round(image.shape[0] * width / image.shape[1]))
        width, height = width // 2 * 2, height // 2 * 2
        image = cv2.resize(image, (width, height))
        ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ok:
            raise RuntimeError("jpeg_failed")
        data = jpeg.tobytes()
        timestamp = datetime.fromisoformat(frame.captured_at).timestamp()
        with self.lock:
            history = (
                self.settings.video_buffer_before
                + self.settings.video_buffer_after
                + self.settings.face_sample_window_seconds
                + 3
            )
            recording.trim(timestamp - history)
            camera_id = next(k[0] for k, r in self.recordings.items() if r is recording)
            camera_bytes = sum(r.bytes for k, r in self.recordings.items() if k[0] == camera_id)
            # Per-session ceiling is supplemented by the aggregate camera check in encode_frames.
            if camera_bytes + len(
                data
            ) > self.settings.video_buffer_max_bytes_per_camera or not self.storage_available(
                len(data)
            ):
                recording.error = "storage_limit"
                recording.dropped += 1
                return
            second = int(timestamp)
            if second != recording.segment_second:
                recording.segment = recording.directory / (uuid.uuid4().hex + ".mjpg")
                recording.segment_second = second
            path = recording.segment
            offset = path.stat().st_size if path.exists() else 0
            with path.open("ab") as stream:
                os.chmod(path, 0o600)
                stream.write(data)
            recording.frames.append(StoredFrame(path, offset, len(data), timestamp))
            recording.bytes += len(data)

    def encode_frames(self):
        while not self.cancel.is_set():
            with self.lock:
                pending = [
                    (key, r, r.pending)
                    for key, r in self.recordings.items()
                    if r.pending is not None
                ]
                for _, r, _ in pending:
                    r.pending = None
                    r.encoding = True
            for _key, recording, frame in pending:
                try:
                    self.append(recording, frame)
                except Exception:
                    recording.error = "buffer_failed"
                    recording.dropped += 1
                finally:
                    with self.lock:
                        recording.encoding = False
            self.cancel.wait(0.01)

    def assemble(self, recording, timestamp):
        before, after = self.settings.video_buffer_before, self.settings.video_buffer_after
        with self.lock:
            frames = [
                f
                for f in recording.frames
                if timestamp - before <= f.timestamp <= timestamp + after
            ]
            if not frames:
                raise RuntimeError(recording.error or "no_frames")
            recording.pins += 1
        directory = self.settings.clip_buffer_dir / uuid.uuid4().hex
        directory.mkdir(mode=0o700)
        name = uuid.uuid4().hex + ".mp4"
        target = self.store.clip_path(name)
        budget = sum(f.size for f in frames) * 3 + 2**20
        reserved = False
        try:
            with self.lock:
                if not self.storage_available(budget):
                    raise RuntimeError("storage_limit")
                self.reserved_bytes += budget
                reserved = True
            manifest = []
            for index, frame in enumerate(frames):
                with frame.path.open("rb") as stream:
                    stream.seek(frame.offset)
                    data = stream.read(frame.size)
                if len(data) != frame.size:
                    raise RuntimeError("buffer_failed")
                path = directory / f"{index:06d}.jpg"
                path.write_bytes(data)
                os.chmod(path, 0o600)
                duration = (
                    frames[index + 1].timestamp - frame.timestamp
                    if index + 1 < len(frames)
                    else 1 / self.settings.clip_fps
                )
                manifest += [f"file '{path.name}'", f"duration {duration:.6f}"]
            manifest.append(f"file '{len(frames) - 1:06d}.jpg'")
            source = directory / "frames.ffconcat"
            source.write_text("ffconcat version 1.0\n" + "\n".join(manifest) + "\n")
            output = directory / "encoded.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "concat",
                    "-safe",
                    "1",
                    "-i",
                    str(source),
                    "-vf",
                    f"fps={self.settings.clip_fps}",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    "-crf",
                    "23",
                    "-threads",
                    "1",
                    "-pix_fmt",
                    "yuv420p",
                    "-an",
                    "-movflags",
                    "+faststart",
                    "-fs",
                    str(budget - sum(f.size for f in frames)),
                    "-t",
                    str(frames[-1].timestamp - frames[0].timestamp + 1 / self.settings.clip_fps),
                    str(output),
                ],
                check=True,
                timeout=self.settings.clip_encode_timeout_seconds,
                capture_output=True,
            )
            if output.stat().st_size >= budget - sum(f.size for f in frames):
                raise RuntimeError("storage_limit")
            if not self.storage_available():
                raise RuntimeError("storage_limit")
            os.chmod(output, 0o600)
            output.replace(target)
            available_before = max(0, timestamp - frames[0].timestamp)
            available_after = max(0, frames[-1].timestamp - timestamp)
            gap = max(
                (b.timestamp - a.timestamp for a, b in zip(frames, frames[1:], strict=False)),
                default=0,
            )
            details = {
                "before_seconds": round(available_before, 3),
                "after_seconds": round(available_after, 3),
                "frames": len(frames),
                "max_gap_seconds": round(gap, 3),
                "audio": False,
                "partial": available_before + 0.2 < before
                or available_after + 0.2 < after
                or gap > 0.5,
                "buffer_error": recording.error,
            }
            return name, details
        except Exception:
            target.unlink(missing_ok=True)
            raise
        finally:
            shutil.rmtree(directory)
            with self.lock:
                recording.pins -= 1
                if reserved:
                    self.reserved_bytes -= budget

    def process_once(self):
        with Session(self.store.engine) as db:
            pending = list(
                db.scalars(
                    select(MatchEvent)
                    .where(MatchEvent.clip_state == "pending", MatchEvent.status != "deleted")
                    .order_by(MatchEvent.id)
                    .limit(self.settings.clip_max_pending + 1)
                )
            )
            for index, event in enumerate(pending):
                if index >= self.settings.clip_max_pending:
                    self.store.finish_clip(event.id, error="queue_limit")
                    continue
                with self.lock:
                    recording = self.recordings.get((event.camera_id, event.stream_session_id))
                    ready = (
                        recording
                        and recording.pending is None
                        and not recording.encoding
                        and (
                            recording.closed
                            or (
                                recording.frames
                                and recording.frames[-1].timestamp
                                >= event.timestamp.replace(tzinfo=UTC).timestamp()
                                + self.settings.video_buffer_after
                            )
                        )
                    )
                if not ready:
                    if (
                        utc_now() - event.timestamp
                    ).total_seconds() > self.settings.video_buffer_after + 15:
                        self.store.finish_clip(event.id, error="buffer_unavailable")
                    continue
                try:
                    # DB datetime is naive UTC, independent of the host timezone.
                    name, details = self.assemble(
                        recording, event.timestamp.replace(tzinfo=UTC).timestamp()
                    )
                    if not self.store.finish_clip(event.id, name=name, details=details):
                        self.store.remove_files([name])
                    else:
                        self.completed += 1
                except Exception as exc:
                    reason = (
                        str(exc)
                        if isinstance(exc, RuntimeError)
                        and str(exc) in {"storage_limit", "no_frames", "buffer_failed"}
                        else "encode_failed"
                    )
                    self.store.finish_clip(event.id, error=reason)
                    self.failed += 1
            keys = {(e.camera_id, e.stream_session_id) for e in pending}
        with self.lock:
            for key, recording in list(self.recordings.items()):
                if (
                    recording.closed
                    and not recording.pending
                    and not recording.encoding
                    and not recording.pins
                    and key not in keys
                    and time.monotonic() - recording.created > 60
                ):
                    shutil.rmtree(recording.directory)
                    del self.recordings[key]

    def process_events(self):
        while not self.cancel.is_set():
            try:
                self.process_once()
                if time.monotonic() - self.last_cleanup > 60:
                    self.store.cleanup()
                    self.last_cleanup = time.monotonic()
            except Exception:
                # An unavailable SQL store or disk must never stop capture/search.
                self.failed += 1
            self.cancel.wait(1)

    def status(self):
        with self.lock:
            return {
                "status": "ready",
                "buffer_bytes": sum(r.bytes for r in self.recordings.values()),
                "sessions": len(self.recordings),
                "dropped_frames": sum(r.dropped for r in self.recordings.values()),
                "completed": self.completed,
                "failures": self.failed,
            }

    def close(self):
        self.cancel.set()
        if self.capture_thread.ident:
            self.capture_thread.join(timeout=3)
        if self.clip_thread.ident:
            self.clip_thread.join(timeout=self.settings.clip_encode_timeout_seconds + 2)
        with self.lock:
            for r in self.recordings.values():
                if not r.pins:
                    shutil.rmtree(r.directory, ignore_errors=True)
