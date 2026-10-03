"""One bounded offline job; every decoded frame runs on the shared GPU scheduler."""

import json
import logging
import math
import shutil
import threading
import time
from concurrent.futures import TimeoutError
from datetime import UTC, datetime, timedelta
from queue import Full

import cv2

from app.core.face_test_data import (
    identifier,
    private_directory,
    storage_size,
    write_metadata,
    write_private,
)
from app.schemas.face_tests import DEFAULT_MIN_FACE_SIZE, FaceTestOptions
from app.worker.video_faces import FaceGroups, VideoTestError, analyze_frame

ACTIVE = {"queued", "running"}


def now():
    return datetime.now(UTC)


class FaceTestManager:
    def __init__(self, settings, runtime):
        self.settings, self.runtime = settings, runtime
        self.root = private_directory(settings.face_test_dir)
        self.incoming = private_directory(self.root / ".incoming")
        self.lock = threading.RLock()
        self.mutation = threading.Lock()
        self.jobs = {}
        self.active = None
        self.cancel = threading.Event()
        self._recover()
        self.cleaner = threading.Thread(
            target=self._maintain, daemon=True, name="face-test-retention"
        )
        self.cleaner.start()

    def _recover(self):
        for directory in self.root.iterdir():
            if not directory.is_dir() or directory.name.startswith(".") or directory.is_symlink():
                continue
            try:
                identifier(directory.name)
                path = directory / "job.json"
                if path.stat().st_size > 2**20:
                    continue
                metadata = json.loads(path.read_text())
                if metadata["job_id"] != directory.name or not isinstance(
                    metadata["owner_id"], int
                ):
                    continue
                if metadata["state"] in ACTIVE:
                    metadata.update(state="failed", error_code="worker_restarted")
                    write_metadata(path, metadata)
                (directory / "source.video").unlink(missing_ok=True)
                self.jobs[directory.name] = metadata
            except (OSError, ValueError, KeyError, TypeError):
                logging.getLogger("cctv.face-tests").warning("Invalid face test metadata")
        self.cleanup()

    def _public(self, metadata):
        result = {key: value for key, value in metadata.items() if key != "owner_id"}
        result["groups"] = [dict(group) for group in metadata["groups"]]
        result["group_count"] = len(result["groups"])
        return result

    def list(self, owner_id):
        with self.lock:
            return [
                self._public(m)
                for m in sorted(self.jobs.values(), key=lambda m: m["created_at"], reverse=True)
                if m["owner_id"] == owner_id and m["expires_at"] > now().isoformat()
            ]

    def get(self, job_id, owner_id):
        identifier(job_id)
        with self.lock:
            metadata = self.jobs.get(job_id)
            if (
                metadata is None
                or metadata["owner_id"] != owner_id
                or metadata["expires_at"] <= now().isoformat()
            ):
                raise KeyError(job_id)
            return self._public(metadata)

    def image(self, job_id, group_id, owner_id):
        with self.lock:
            metadata = self.get(job_id, owner_id)
            if not any(g["group_id"] == group_id for g in metadata["groups"]):
                raise KeyError(group_id)
            try:
                return (self.root / job_id / f"{group_id}.jpg").read_bytes()
            except OSError:
                raise KeyError(group_id) from None

    def start(
        self,
        job_id,
        owner_id,
        filename,
        *,
        detection_threshold=None,
        min_face_size=DEFAULT_MIN_FACE_SIZE,
    ):
        identifier(job_id)
        options = FaceTestOptions(
            detection_threshold=(
                self.settings.face_detection_threshold
                if detection_threshold is None
                else detection_threshold
            ),
            min_face_size=min_face_size,
        )
        source = self.incoming / f"{job_id}.video"
        with self.mutation, self.lock:
            if self.cancel.is_set() or self.runtime.face_analyzer is None:
                raise RuntimeError("Face analysis unavailable")
            if self.active is not None:
                raise OverflowError("Another video test is running")
            if len(self.list(owner_id)) >= self.settings.face_test_max_jobs_per_user:
                raise OverflowError("Face test history limit reached")
            if len(self.jobs) >= 100:
                raise OverflowError("Face test history limit reached")
            if not source.is_file() or source.is_symlink() or job_id in self.jobs:
                raise ValueError("Invalid video test upload")
            if storage_size(self.root) > self.settings.face_test_storage_max_mb * 2**20:
                raise OverflowError("Face test storage limit reached")
            directory = private_directory(self.root / job_id)
            source.rename(directory / "source.video")
            created = now()
            metadata = {
                "job_id": job_id,
                "owner_id": owner_id,
                "filename": filename,
                "state": "queued",
                "error_code": None,
                "created_at": created.isoformat(),
                "expires_at": (
                    created + timedelta(hours=self.settings.face_test_retention_hours)
                ).isoformat(),
                "processed_frames": 0,
                "total_frames": None,
                "expected_frames": None,
                "detections": 0,
                "duration_seconds": None,
                "progress_percent": 0,
                "threshold": self.runtime.settings.face_match_threshold,
                **options.model_dump(),
                "actual_device": self.runtime.face_analyzer.info.get("actual_device", "unknown"),
                "groups": [],
            }
            try:
                write_metadata(directory / "job.json", metadata)
                self.jobs[job_id] = metadata
                cancellation = threading.Event()
                thread = threading.Thread(
                    target=self._run,
                    args=(job_id, cancellation),
                    daemon=True,
                    name="face-test-video",
                )
                self.active = (job_id, cancellation, thread)
                thread.start()
            except Exception:
                self.active = None
                self.jobs.pop(job_id, None)
                shutil.rmtree(directory, ignore_errors=True)
                raise
            return self._public(metadata)

    def _save(self, job_id, **updates):
        with self.lock:
            metadata = self.jobs[job_id]
            metadata.update(updates)
            write_metadata(self.root / job_id / "job.json", metadata)

    def _infer(self, image, cancellation, options):
        while True:
            if cancellation.is_set() or self.cancel.is_set():
                raise VideoTestError("cancelled")
            try:
                future = self.runtime.submit_face_task(
                    lambda analyzer: analyze_frame(
                        analyzer,
                        image,
                        self.settings.face_test_max_faces_per_frame,
                        cancellation,
                        **options,
                    )
                )
                break
            except Full:
                cancellation.wait(0.02)
        while True:
            try:
                return future.result(timeout=0.2)
            except TimeoutError:
                if cancellation.is_set() or self.cancel.is_set():
                    if future.cancel():
                        raise VideoTestError("cancelled") from None
                    # A running CUDA call observes cancellation between faces/tiles;
                    # wait for it before allowing deletion or another job.

    def _run(self, job_id, cancellation):
        directory = self.root / job_id
        cap = None
        groups = FaceGroups(self.jobs[job_id]["threshold"], self.settings.face_test_max_groups)
        options = {key: self.jobs[job_id][key] for key in ("detection_threshold", "min_face_size")}
        processed = detections = 0
        try:
            cap = cv2.VideoCapture(
                str(directory / "source.video"),
                cv2.CAP_FFMPEG,
                [cv2.CAP_PROP_N_THREADS, self.settings.capture_decode_threads],
            )
            width, height = cap.get(cv2.CAP_PROP_FRAME_WIDTH), cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
            if not cap.isOpened() or not 0 < width <= 3840 or not 0 < height <= 2160:
                raise VideoTestError("invalid_video")
            fps = cap.get(cv2.CAP_PROP_FPS)
            fps = fps if math.isfinite(fps) and fps > 0 else 25
            expected = cap.get(cv2.CAP_PROP_FRAME_COUNT)
            expected = int(expected) if math.isfinite(expected) and expected > 0 else None
            duration = expected / fps if expected else None
            if duration and duration > self.settings.face_test_max_duration_seconds:
                raise VideoTestError("duration_limit_exceeded")
            self._save(
                job_id,
                state="running",
                expected_frames=expected,
                total_frames=expected,
                duration_seconds=round(duration, 3) if duration else None,
            )
            last_save = 0
            previous_seconds = -1
            while not cancellation.is_set() and not self.cancel.is_set():
                ok, image = cap.read()
                if not ok:
                    break
                if image.shape[1] > 3840 or image.shape[0] > 2160:
                    raise VideoTestError("invalid_video")
                # Decode consecutively; never seek, drop or skip a frame.
                seconds = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
                if not math.isfinite(seconds) or seconds < 0 or seconds <= previous_seconds:
                    seconds = max(previous_seconds + 1 / fps, processed / fps)
                previous_seconds = seconds
                if seconds > self.settings.face_test_max_duration_seconds or processed >= 432000:
                    raise VideoTestError("duration_limit_exceeded")
                faces = self._infer(image, cancellation, options)
                processed += 1
                detections += len(faces)
                for group_id, content in groups.add_frame(faces, processed, seconds):
                    write_private(directory / f"{group_id}.jpg", content)
                current = time.monotonic()
                if current - last_save >= 0.5:
                    if (
                        storage_size(self.root) > self.settings.face_test_storage_max_mb * 2**20
                        or shutil.disk_usage(self.root).free < self.settings.storage_min_free_bytes
                    ):
                        raise VideoTestError("storage_limit_exceeded")
                    self._save(
                        job_id,
                        processed_frames=processed,
                        detections=detections,
                        progress_percent=min(99, round(processed / expected * 100, 1))
                        if expected
                        else None,
                        groups=groups.metadata(),
                    )
                    last_save = current
            if cancellation.is_set() or self.cancel.is_set():
                raise VideoTestError("cancelled")
            if not processed:
                raise VideoTestError("invalid_video")
            if expected and processed < expected:
                raise VideoTestError("video_decode_incomplete")
            self._save(
                job_id,
                state="completed",
                progress_percent=100,
                total_frames=processed,
                processed_frames=processed,
                detections=detections,
                groups=groups.metadata(),
            )
        except Exception as exc:
            code = exc.code if isinstance(exc, VideoTestError) else "analysis_failed"
            self._save(
                job_id,
                state="cancelled" if code == "cancelled" else "failed",
                error_code=code,
                processed_frames=processed,
                detections=detections,
                groups=[
                    g for g in groups.metadata() if (directory / f"{g['group_id']}.jpg").is_file()
                ],
            )
            logging.getLogger("cctv.face-tests").info(
                "Video test ended; code=%s type=%s", code, type(exc).__name__
            )
        finally:
            if cap is not None:
                cap.release()
            (directory / "source.video").unlink(missing_ok=True)
            # Groups, their vectors and frame buffers are local to this thread.
            with self.lock:
                self.active = None

    def delete_all(self, owner_id):
        with self.mutation:
            with self.lock:
                identifiers = [key for key, m in self.jobs.items() if m["owner_id"] == owner_id]
                active = self.active if self.active and self.active[0] in identifiers else None
                if active:
                    active[1].set()
            if active:
                active[2].join(timeout=20)
                if active[2].is_alive():
                    raise TimeoutError("Video analysis is stopping")
            with self.lock:
                for job_id in identifiers:
                    shutil.rmtree(self.root / job_id, ignore_errors=False)
                    self.jobs.pop(job_id, None)
            return {"deleted_jobs": len(identifiers)}

    def cleanup(self):
        with self.mutation, self.lock:
            if self.active and self.jobs[self.active[0]]["expires_at"] <= now().isoformat():
                self.active[1].set()
            for job_id, metadata in list(self.jobs.items()):
                if metadata["state"] not in ACTIVE and metadata["expires_at"] <= now().isoformat():
                    shutil.rmtree(self.root / job_id, ignore_errors=True)
                    self.jobs.pop(job_id, None)
            for path in self.incoming.glob("*.video"):
                if time.time() - path.stat().st_mtime > 3600:
                    path.unlink(missing_ok=True)

    def _maintain(self):
        while not self.cancel.wait(30):
            try:
                self.cleanup()
            except OSError:
                logging.getLogger("cctv.face-tests").warning(
                    "Video test retention cleanup deferred"
                )

    def close(self):
        self.cancel.set()
        with self.lock:
            active = self.active
            if active:
                active[1].set()
        if active:
            active[2].join(timeout=10)
        self.cleaner.join(timeout=2)
