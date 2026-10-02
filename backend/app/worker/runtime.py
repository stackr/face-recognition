import logging
import os
import threading
import time
import uuid
from collections import Counter, OrderedDict, deque
from dataclasses import dataclass
from datetime import UTC, datetime

import cv2
import numpy as np

from app.worker.faces import TrackFaces
from app.worker.tracker import CameraTracker

ACTIVE = {"opening", "running", "draining", "stopping"}


@dataclass(frozen=True)
class Frame:
    image: np.ndarray
    stream_session_id: str
    frame_id: int
    captured_at: str
    captured_mono: float


class CameraRun:
    def __init__(self, camera_id, source, source_type, loop, settings):
        self.camera_id, self.source, self.source_type = camera_id, source, source_type
        self.loop, self.settings = loop, settings
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.stream_session_id = uuid.uuid4().hex
        self.tracker = None
        self.faces = None
        self.latest = None
        self.jpeg = None
        self.result = None
        self.state, self.error_code = "opening", None
        self.created_mono = time.monotonic()
        self.ended_mono = None
        self.next_due = 0
        self.captured = self.processed = self.dropped = 0
        self.loops = 0
        self.input_fps = 0
        self.resolution = None
        self.latencies = deque(maxlen=600)
        self.detection_ms = deque(maxlen=600)
        self.face_counts = Counter()
        self.face_ms = deque(maxlen=600)
        self.max_people = 0
        self.thread = threading.Thread(
            target=self.capture, daemon=True, name=f"capture-{camera_id}"
        )

    def capture(self):
        cap = None
        try:
            # OpenCV/FFmpeg errors must never echo an RTSP password to stderr.
            os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
            if self.source_type == "rtsp":
                cap = cv2.VideoCapture(
                    self.source,
                    cv2.CAP_FFMPEG,
                    [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 3000],
                )
            else:
                cap = cv2.VideoCapture(self.source, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                self.fail("source_open_failed")
                return
            if (
                cap.get(cv2.CAP_PROP_FRAME_WIDTH) > 3840
                or cap.get(cv2.CAP_PROP_FRAME_HEIGHT) > 2160
            ):
                self.fail("source_resolution_exceeded")
                return
            fps = float(cap.get(cv2.CAP_PROP_FPS))
            self.input_fps = fps if np.isfinite(fps) and 0 < fps <= 240 else 25
            origin = time.monotonic()
            index = 0
            while not self.cancel.is_set():
                ok, image = cap.read()
                if not ok:
                    if self.source_type == "mp4" and self.loop and index:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        with self.lock:
                            self.stream_session_id = uuid.uuid4().hex
                            self.tracker = self.faces = None
                            self.latest = self.jpeg = self.result = None
                            self.loops += 1
                        origin, index = time.monotonic(), 0
                        continue
                    if self.source_type == "mp4" and index:
                        with self.lock:
                            self.state = "draining"
                        return
                    self.fail("source_read_failed")
                    return
                if image.shape[0] > 2160 or image.shape[1] > 3840:
                    self.fail("source_resolution_exceeded")
                    return
                if self.source_type == "mp4":
                    self.cancel.wait(max(0, origin + index / self.input_fps - time.monotonic()))
                if self.cancel.is_set():
                    return
                index += 1
                with self.lock:
                    self.captured += 1
                    if self.latest is not None:
                        self.dropped += 1
                    self.resolution = [image.shape[1], image.shape[0]]
                    self.latest = Frame(
                        image,
                        self.stream_session_id,
                        self.captured,
                        datetime.now(UTC).isoformat(),
                        time.monotonic(),
                    )
                    # "running" means a successfully analysed frame, not only an open source.
        except Exception:
            self.fail("capture_failed")
        finally:
            if cap is not None:
                cap.release()
            self.source = ""  # Erase credentials after the capture thread exits.

    def fail(self, code):
        with self.lock:
            self.state, self.error_code = "error", code
            self.latest = self.jpeg = self.result = self.tracker = self.faces = None
            self.ended_mono = time.monotonic()
        self.cancel.set()
        logging.getLogger("cctv.worker").warning("Camera %s failed; code=%s", self.camera_id, code)

    def status(self):
        with self.lock:
            elapsed = max(0.001, (self.ended_mono or time.monotonic()) - self.created_mono)
            return {
                "camera_id": self.camera_id,
                "state": self.state,
                "error_code": self.error_code,
                "source_type": self.source_type,
                "stream_session_id": self.stream_session_id,
                "loop": self.loop,
                "loops": self.loops,
                "resolution": self.resolution,
                "input_fps": round(self.input_fps, 2),
                "elapsed_seconds": round(elapsed, 2),
                "captured_frames": self.captured,
                "processed_frames": self.processed,
                "dropped_frames": self.dropped,
                "pending_frames": int(self.latest is not None),
                "capture_fps": round(self.captured / elapsed, 2),
                "detection_fps": round(self.processed / elapsed, 2),
                "face_analysis_fps": round(self.face_counts["analysis_frames"] / elapsed, 2),
                "face_roi_fps": round(self.face_counts["roi_attempts"] / elapsed, 2),
                "face_counts": dict(self.face_counts),
                "face_cache_tracks": self.faces.size() if self.faces else 0,
                "face_mean_ms": round(float(np.mean(self.face_ms)), 2) if self.face_ms else None,
                "max_people": self.max_people,
                "latency_mean_ms": round(float(np.mean(self.latencies)), 2)
                if self.latencies
                else None,
                "latency_p95_ms": round(float(np.percentile(self.latencies, 95)), 2)
                if self.latencies
                else None,
                "detection_mean_ms": round(float(np.mean(self.detection_ms)), 2)
                if self.detection_ms
                else None,
                "latency_samples": len(self.latencies),
                "result": self.result,
            }


class WorkerRuntime:
    def __init__(self, settings, detector, face_analyzer=None):
        self.settings, self.detector = settings, detector
        self.face_analyzer = face_analyzer
        self.lock = threading.RLock()
        self.runs = OrderedDict()
        self.cancel = threading.Event()
        self.scheduler = threading.Thread(target=self.schedule, daemon=True, name="shared-gpu")
        self.scheduler.start()

    def start(self, camera_id, source, source_type, loop):
        with self.lock:
            previous = self.runs.get(camera_id)
            if previous and (previous.state in ACTIVE or previous.thread.is_alive()):
                raise ValueError("Camera already active")
            if (
                sum(run.state in ACTIVE or run.thread.is_alive() for run in self.runs.values())
                >= self.settings.max_active_cameras
            ):
                raise OverflowError("Active camera limit reached")
            run = CameraRun(camera_id, source, source_type, loop, self.settings)
            self.runs[camera_id] = run
            self.runs.move_to_end(camera_id)
            # Retain only bounded terminal status history; no images in stopped runs.
            for identifier, old in list(self.runs.items()):
                if len(self.runs) <= 64:
                    break
                if old.state not in ACTIVE and not old.thread.is_alive():
                    del self.runs[identifier]
            run.thread.start()
            return run.status()

    def get(self, camera_id):
        with self.lock:
            if camera_id not in self.runs:
                raise KeyError(camera_id)
            return self.runs[camera_id]

    def stop(self, camera_id):
        run = self.get(camera_id)
        run.cancel.set()
        with run.lock:
            run.state = "stopping"
        run.thread.join(timeout=6)
        if run.thread.is_alive():
            raise TimeoutError("Capture did not stop")
        with run.lock:
            run.state = "stopped"
            run.ended_mono = run.ended_mono or time.monotonic()
            run.latest = run.jpeg = run.result = run.tracker = run.faces = None
            run.source = ""
        return run.status()

    def schedule(self):
        while not self.cancel.is_set():
            with self.lock:
                runs = list(self.runs.values())
            for run in runs:
                if run.cancel.is_set():
                    continue
                with run.lock:
                    if run.state == "draining" and run.latest is None:
                        run.state = "ended"
                        run.ended_mono = time.monotonic()
                        run.jpeg = run.result = run.tracker = run.faces = None
                        continue
                    if run.latest is None or time.monotonic() < run.next_due:
                        continue
                    frame, run.latest = run.latest, None
                    run.next_due = time.monotonic() + 1 / self.settings.detection_fps
                try:
                    start = time.monotonic()
                    boxes = self.detector.detect(frame.image)
                    detection_ms = (time.monotonic() - start) * 1000
                    with run.lock:
                        if run.cancel.is_set() or frame.stream_session_id != run.stream_session_id:
                            continue
                        if run.tracker is None:
                            run.tracker = CameraTracker(self.settings)
                        tracks = run.tracker.update(boxes, frame.image, frame.captured_mono)
                        if run.faces is None:
                            run.faces = TrackFaces(self.settings)
                        faces = run.faces
                        live_ids = run.tracker.live_ids()
                    face_start = time.monotonic()
                    face_counts = Counter()
                    if self.face_analyzer is not None:
                        face_counts = faces.process(self.face_analyzer, frame, tracks, live_ids)
                    face_ms = (time.monotonic() - face_start) * 1000
                    annotated = frame.image.copy()
                    for track in tracks:
                        x1, y1, x2, y2 = map(int, track["bbox"])
                        cv2.rectangle(annotated, (x1, y1), (x2, y2), (80, 230, 120), 2)
                        cv2.putText(
                            annotated,
                            f"person #{track['track_id']} {track['confidence']:.2f}",
                            (max(0, x1), max(20, y1 - 8)),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.6,
                            (80, 230, 120),
                            2,
                        )
                        face = track.get("face", {})
                        if face.get("bbox") and face.get("frame_id") == frame.frame_id:
                            fx1, fy1, fx2, fy2 = map(int, face["bbox"])
                            color = (
                                (100, 220, 255) if face["status"] == "accepted" else (100, 130, 220)
                            )
                            cv2.rectangle(annotated, (fx1, fy1), (fx2, fy2), color, 2)
                            for px, py in face.get("landmarks", []):
                                cv2.circle(annotated, (round(px), round(py)), 2, color, -1)
                    label = f"cam {run.camera_id} | {frame.stream_session_id[:8]} | frame {frame.frame_id} | {frame.captured_at}"
                    cv2.putText(
                        annotated,
                        label,
                        (12, 24),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.45,
                        (255, 255, 255),
                        1,
                    )
                    if annotated.shape[1] > 1280:
                        annotated = cv2.resize(
                            annotated, (1280, round(annotated.shape[0] * 1280 / annotated.shape[1]))
                        )
                    ok, encoded = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    if not ok:
                        raise RuntimeError("JPEG encoding failed")
                    with run.lock:
                        # Stop/EOF/loop can happen during inference; never publish stale faces.
                        if run.cancel.is_set() or frame.stream_session_id != run.stream_session_id:
                            continue
                        run.jpeg = encoded.tobytes()
                        run.result = {
                            "stream_session_id": frame.stream_session_id,
                            "frame_id": frame.frame_id,
                            "captured_at": frame.captured_at,
                            "tracks": tracks,
                        }
                        run.processed += 1
                        run.face_counts.update(face_counts)
                        if face_counts["analysis_frames"]:
                            run.face_ms.append(face_ms)
                        run.max_people = max(run.max_people, len(tracks))
                        run.detection_ms.append(detection_ms)
                        run.latencies.append((time.monotonic() - frame.captured_mono) * 1000)
                        if run.state != "draining":
                            run.state = "running"
                except Exception as exc:
                    logging.getLogger("cctv.worker").error(
                        "Camera %s inference failed; type=%s", run.camera_id, type(exc).__name__
                    )
                    run.fail("inference_failed")
            self.cancel.wait(0.005)

    def close(self):
        self.cancel.set()
        with self.lock:
            runs = list(self.runs.values())
        for run in runs:
            try:
                self.stop(run.camera_id)
            except TimeoutError:
                pass
        self.scheduler.join(timeout=10)
