import logging
import os
import threading
import time
import uuid
from collections import Counter, OrderedDict, deque
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from queue import Empty, Full, Queue

import cv2
import numpy as np

from app.worker.faces import TrackFaces
from app.worker.reconnect import reconnect_delay
from app.worker.tracker import CameraTracker

ACTIVE = {"opening", "running", "reconnecting", "draining", "stopping"}


@dataclass(frozen=True)
class Frame:
    image: np.ndarray
    stream_session_id: str
    frame_id: int
    captured_at: str
    captured_mono: float


class CameraRun:
    def __init__(
        self, camera_id, source, source_type, loop, settings, actual_device=None, clips=None
    ):
        self.camera_id, self.source, self.source_type = camera_id, source, source_type
        self.loop, self.settings = loop, settings
        self.actual_device = actual_device
        self.clips = clips
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.stream_session_id = uuid.uuid4().hex
        self.tracker = None
        self.faces = None
        self.reid = None
        self.latest = None
        self.jpeg = None
        self.result = None
        self.state, self.error_code = "opening", None
        self.created_mono = time.monotonic()
        self.ended_mono = None
        self.next_due = 0
        self.captured = self.processed = self.dropped = 0
        self.loops = 0
        self.connection_attempts = self.reconnects = self.consecutive_failures = 0
        self.next_retry_mono = self.next_retry_at = None
        self.connected_at = self.last_frame_at = self.last_frame_mono = None
        self.session_captured = self.session_processed = 0
        self.input_fps = 0
        self.resolution = None
        self.latencies = deque(maxlen=600)
        self.detection_ms = deque(maxlen=600)
        self.face_counts = Counter()
        self.face_ms = deque(maxlen=600)
        self.face_process_ms = deque(maxlen=600)
        self.face_search_ms = deque(maxlen=600)
        self.reid_counts = Counter()
        self.reid_ms = deque(maxlen=600)
        self.max_people = 0
        self.thread = threading.Thread(
            target=self.capture, daemon=True, name=f"capture-{camera_id}"
        )

    def capture(self):
        try:
            if self.source_type == "rtsp":
                self.capture_rtsp()
            else:
                self.capture_mp4()
        except Exception:
            self.fail("capture_failed")
        finally:
            if self.clips:
                self.clips.end_session(self.camera_id, self.stream_session_id)
            with self.lock:
                self.source = ""  # Erase credentials after the capture thread exits.
                if self.state == "stopping":
                    self.state = "stopped"
                    self.ended_mono = self.ended_mono or time.monotonic()
                    self.clear_session()

    def clear_session(self, *, rotate=False):
        # Caller holds run.lock; replacing both objects drops candidates and all
        # per-track state, including event submission cooldowns.
        if self.clips:
            self.clips.end_session(self.camera_id, self.stream_session_id)
        if rotate:
            self.stream_session_id = uuid.uuid4().hex
            self.session_captured = self.session_processed = 0
            self.connected_at = None
        self.latest = self.jpeg = self.result = self.tracker = self.faces = self.reid = None
        self.next_due = 0

    def queue_frame(self, image):
        now = time.monotonic()
        with self.lock:
            if self.cancel.is_set():
                return
            self.captured += 1
            self.session_captured += 1
            if self.latest is not None:
                self.dropped += 1
            self.resolution = [image.shape[1], image.shape[0]]
            self.last_frame_at = datetime.now(UTC).isoformat()
            self.last_frame_mono = now
            frame = self.latest = Frame(
                image, self.stream_session_id, self.session_captured, self.last_frame_at, now
            )

        if self.clips:
            self.clips.offer(self.camera_id, frame)

    def retry(self, code):
        with self.lock:
            if self.cancel.is_set():
                return
            self.consecutive_failures += 1
            delay = reconnect_delay(self.settings, self.consecutive_failures)
            self.clear_session(rotate=True)
            self.state, self.error_code = "reconnecting", code
            self.next_retry_mono = time.monotonic() + delay
            self.next_retry_at = (datetime.now(UTC) + timedelta(seconds=delay)).isoformat()
        logging.getLogger("cctv.worker").warning(
            "Camera %s reconnect pending; code=%s delay=%.2f", self.camera_id, code, delay
        )
        self.cancel.wait(delay)

    def capture_rtsp(self):
        os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
        while not self.cancel.is_set():
            cap = None
            failure = "source_open_failed"
            first_frame_mono = None
            with self.lock:
                self.connection_attempts += 1
                attempt = self.connection_attempts
                self.next_retry_at = self.next_retry_mono = None
            try:
                cap = cv2.VideoCapture(
                    self.source,
                    cv2.CAP_FFMPEG,
                    [
                        cv2.CAP_PROP_N_THREADS,
                        self.settings.capture_decode_threads,
                        cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,
                        round(self.settings.rtsp_open_timeout_seconds * 1000),
                        cv2.CAP_PROP_READ_TIMEOUT_MSEC,
                        round(self.settings.rtsp_read_timeout_seconds * 1000),
                    ],
                )
                if self.cancel.is_set():
                    return
                if cap.isOpened():
                    if (
                        cap.get(cv2.CAP_PROP_FRAME_WIDTH) > 3840
                        or cap.get(cv2.CAP_PROP_FRAME_HEIGHT) > 2160
                    ):
                        self.fail("source_resolution_exceeded")
                        return
                    fps = float(cap.get(cv2.CAP_PROP_FPS))
                    with self.lock:
                        self.input_fps = fps if np.isfinite(fps) and 0 < fps <= 240 else 25
                    failure = "source_read_failed"
                    while not self.cancel.is_set():
                        ok, image = cap.read()
                        if self.cancel.is_set():
                            return
                        if not ok or image is None:
                            break
                        if image.shape[0] > 2160 or image.shape[1] > 3840:
                            self.fail("source_resolution_exceeded")
                            return
                        now = time.monotonic()
                        with self.lock:
                            if first_frame_mono is None:
                                first_frame_mono = now
                                self.connected_at = datetime.now(UTC).isoformat()
                                self.reconnects += int(attempt > 1)
                                logging.getLogger("cctv.worker").info(
                                    "Camera %s stream connected; attempt=%s",
                                    self.camera_id,
                                    attempt,
                                )
                            if now - first_frame_mono >= self.settings.rtsp_reconnect_reset_seconds:
                                self.consecutive_failures = 0
                        self.queue_frame(image)
            except Exception:
                failure = "capture_failed"
            finally:
                if cap is not None:
                    cap.release()
            self.retry(failure)

    def capture_mp4(self):
        cap = None
        try:
            cap = cv2.VideoCapture(
                self.source,
                cv2.CAP_FFMPEG,
                [cv2.CAP_PROP_N_THREADS, self.settings.capture_decode_threads],
            )
            if self.cancel.is_set():
                return
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
                    if self.loop and index:
                        if not cap.set(cv2.CAP_PROP_POS_FRAMES, 0):
                            self.fail("video_seek_failed")
                            return
                        with self.lock:
                            self.clear_session(rotate=True)
                            self.loops += 1
                        origin, index = time.monotonic(), 0
                        continue
                    if index:
                        with self.lock:
                            self.state = "draining"
                        return
                    self.fail("source_read_failed")
                    return
                if image.shape[0] > 2160 or image.shape[1] > 3840:
                    self.fail("source_resolution_exceeded")
                    return
                self.cancel.wait(max(0, origin + index / self.input_fps - time.monotonic()))
                if self.cancel.is_set():
                    return
                index += 1
                self.queue_frame(image)
        finally:
            if cap is not None:
                cap.release()

    def fail(self, code):
        with self.lock:
            if self.cancel.is_set() and self.state in {"stopping", "stopped"}:
                return
            self.state, self.error_code = "error", code
            self.clear_session()
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
                "actual_device": self.actual_device,
                "stream_session_id": self.stream_session_id,
                "loop": self.loop,
                "loops": self.loops,
                "connection_attempts": self.connection_attempts,
                "reconnect_attempts": max(0, self.connection_attempts - 1),
                "reconnects": self.reconnects,
                "consecutive_failures": self.consecutive_failures,
                "next_retry_at": self.next_retry_at,
                "next_retry_seconds": round(max(0, self.next_retry_mono - time.monotonic()), 2)
                if self.next_retry_mono is not None
                else None,
                "connected_at": self.connected_at,
                "last_frame_at": self.last_frame_at,
                "last_frame_age_seconds": round(time.monotonic() - self.last_frame_mono, 2)
                if self.last_frame_mono is not None
                else None,
                "session_captured_frames": self.session_captured,
                "session_processed_frames": self.session_processed,
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
                "reid_counts": dict(self.reid_counts),
                "reid_cache_tracks": self.reid.size() if self.reid else 0,
                "reid_mean_ms": round(float(np.mean(self.reid_ms)), 2) if self.reid_ms else None,
                "face_cache_tracks": self.faces.size() if self.faces else 0,
                "face_mean_ms": round(float(np.mean(self.face_ms)), 2) if self.face_ms else None,
                "face_process_mean_ms": round(float(np.mean(self.face_process_ms)), 2)
                if self.face_process_ms
                else None,
                "face_search_mean_ms": round(float(np.mean(self.face_search_ms)), 2)
                if self.face_search_ms
                else None,
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
                "detection_p95_ms": round(float(np.percentile(self.detection_ms, 95)), 2)
                if self.detection_ms
                else None,
                "face_p95_ms": round(float(np.percentile(self.face_ms, 95)), 2)
                if self.face_ms
                else None,
                "embedding_fps": round(self.face_counts["embeddings_created"] / elapsed, 2),
                "latency_samples": len(self.latencies),
                "result": self.result,
            }


class WorkerRuntime:
    def __init__(
        self, settings, detector, face_analyzer=None, gallery=None, events=None, sampling_revision=0
    ):
        self.settings, self.detector = settings, detector
        self.face_analyzer = face_analyzer
        self.gallery = gallery
        self.events = events
        self.reidentifier = None
        self.reid_status = {"status": "disabled"}
        self.diagnostics = None
        self.clips = None
        self.face_tests = None
        self.sampling_revision = sampling_revision
        self.pending_sampling = None
        self.configure_detector()
        self.batch_counts = Counter()
        self.batch_ms = deque(maxlen=600)
        self.rotation = 0
        self.commands = Queue(maxsize=2)
        self.lock = threading.RLock()
        self.runs = OrderedDict()
        self.cancel = threading.Event()
        self.scheduler = threading.Thread(target=self.schedule, daemon=True, name="shared-gpu")
        self.scheduler.start()

    def configure_detector(self):
        configure = getattr(self.detector, "configure_confidence", None)
        if configure is not None:
            configure(self.settings.detection_confidence)

    def sampling_status(self):
        from app.schemas.recognition import SamplingSettings

        with self.lock:
            return {
                "revision": self.sampling_revision,
                "values": SamplingSettings.defaults(self.settings).model_dump(),
            }

    def queue_sampling(self, revision, values):
        with self.lock:
            latest = self.pending_sampling[0] if self.pending_sampling else self.sampling_revision
            if revision > latest:
                self.pending_sampling = (revision, values)

    def apply_sampling(self):
        # Only the shared inference thread mutates sampling controls, between frames.
        with self.lock:
            if self.pending_sampling is None:
                return
            revision, values = self.pending_sampling
            self.pending_sampling = None
            for name, value in values.model_dump().items():
                setattr(self.settings, name, value)
            self.configure_detector()
            for run in self.runs.values():
                with run.lock:
                    run.next_due = 0
                    if run.tracker:
                        run.tracker.fps = values.detection_fps
                        run.tracker.tracker.max_frames_lost = max(
                            1, round(self.settings.track_lost_seconds * values.detection_fps)
                        )
            if self.face_analyzer:
                self.face_analyzer.info.setdefault("quality", {}).update(
                    interval_seconds=values.face_analysis_interval,
                    max_rois_per_frame=values.face_rois_per_frame,
                )
            self.sampling_revision = revision

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
            run = CameraRun(
                camera_id,
                source,
                source_type,
                loop,
                self.settings,
                self.detector.info["actual_device"],
                self.clips,
            )
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
            run.clear_session()
            run.next_retry_at = run.next_retry_mono = None
        run.thread.join(
            timeout=max(
                6,
                self.settings.rtsp_open_timeout_seconds + 1,
                self.settings.rtsp_read_timeout_seconds + 1,
            )
        )
        if run.thread.is_alive():
            raise TimeoutError("Capture did not stop")
        with run.lock:
            run.state = "stopped"
            run.ended_mono = run.ended_mono or time.monotonic()
            run.clear_session()
            run.source = ""
        return run.status()

    def schedule(self):
        while not self.cancel.is_set():
            self.apply_sampling()
            try:
                future, content = self.commands.get_nowait()
            except Empty:
                pass
            else:
                if future.set_running_or_notify_cancel():
                    try:
                        from app.worker.reference_images import analyze_reference

                        future.set_result(
                            content(self.face_analyzer)
                            if callable(content)
                            else analyze_reference(self.face_analyzer, content)
                        )
                    except Exception as exc:
                        future.set_exception(exc)
                # Do not keep the last upload/frame or returned vectors alive while idle.
                del future, content
            with self.lock:
                runs = list(self.runs.values())
            if runs:
                offset = self.rotation % len(runs)
                runs = runs[offset:] + runs[:offset]
                self.rotation += 1
            face_cost = max(
                (float(np.mean(r.face_ms)) for r in runs if getattr(r, "face_ms", None)), default=0
            )
            limit = self.settings.detector_batch_size
            if face_cost > 0:
                limit = min(
                    limit, max(1, int(self.settings.detector_batch_face_budget_ms / face_cost))
                )
            for offset in range(0, len(runs), limit):
                batch = []
                # Select the latest image immediately before its batch, including
                # after earlier face work. This bounds avoidable capture staleness.
                for run in runs[offset : offset + limit]:
                    if run.cancel.is_set():
                        continue
                    with run.lock:
                        if run.state == "draining" and run.latest is None:
                            run.state = "ended"
                            run.ended_mono = time.monotonic()
                            run.jpeg = run.result = run.tracker = run.faces = run.reid = None
                            continue
                        if run.latest is None or time.monotonic() < run.next_due:
                            continue
                        frame, run.latest = run.latest, None
                        run.next_due = time.monotonic() + 1 / self.settings.detection_fps
                    batch.append((run, frame))
                if not batch:
                    continue
                try:
                    start = time.monotonic()
                    images = [frame.image for _, frame in batch]
                    boxes = (
                        self.detector.detect_batch(images)
                        if hasattr(self.detector, "detect_batch")
                        else [self.detector.detect(image) for image in images]
                    )
                    detection_ms = (time.monotonic() - start) * 1000 / len(batch)
                    if len(boxes) != len(batch):
                        raise RuntimeError("Detector batch result count mismatch")
                    self.batch_counts[len(batch)] += 1
                    self.batch_ms.append(detection_ms * len(batch))
                except Exception:
                    for run, frame in batch:
                        with run.lock:
                            if (
                                not run.cancel.is_set()
                                and frame.stream_session_id == run.stream_session_id
                            ):
                                run.fail("inference_failed")
                    continue
                for (run, frame), frame_boxes in zip(batch, boxes, strict=True):
                    self.process_frame(run, frame, frame_boxes, detection_ms)
            self.cancel.wait(0.005)

    def scheduler_status(self):
        return {
            "max_batch_size": self.settings.detector_batch_size,
            "opencv_threads": self.settings.opencv_threads,
            "capture_decode_threads": self.settings.capture_decode_threads,
            "face_budget_ms": self.settings.detector_batch_face_budget_ms,
            "batches_by_size": dict(self.batch_counts),
            "batch_mean_ms": round(float(np.mean(self.batch_ms)), 2) if self.batch_ms else None,
            "detection_timing": "batch inference including transfers/NMS divided by batch size",
            "policy": "rotating ready cameras; latest frame only; no batching wait; shrink batches for costly face analysis",
        }

    def process_frame(self, run, frame, boxes, detection_ms):
        try:
            with run.lock:
                if run.cancel.is_set() or frame.stream_session_id != run.stream_session_id:
                    return
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
            face_process_ms = (time.monotonic() - face_start) * 1000
            search_start = time.monotonic()
            gallery_revision = None
            search_status = "disabled"
            if self.gallery is not None:
                try:
                    gallery_revision = faces.match(self.gallery, tracks)
                    search_status = "ready"
                except Exception as exc:
                    # Reference-store outages must not stop camera analysis.
                    search_status = "unavailable"
                    logging.getLogger("cctv.worker").warning(
                        "Reference search failed; type=%s", type(exc).__name__
                    )
            face_search_ms = (time.monotonic() - search_start) * 1000
            face_ms = (time.monotonic() - face_start) * 1000
            reid_counts = Counter()
            reid_start = time.monotonic()
            if self.reidentifier is not None:
                from app.worker.reid import BodyTrackCache

                with run.lock:
                    if run.cancel.is_set() or frame.stream_session_id != run.stream_session_id:
                        return
                    if run.reid is None:
                        run.reid = BodyTrackCache(self.settings)
                    reid = run.reid
                try:
                    reid_counts = reid.process(self.reidentifier, frame, tracks, live_ids)
                except Exception:
                    reid_counts["failures"] += 1
                    for track in tracks:
                        track["reid"] = {"status": "unavailable", "embedding_ready": False}
            reid_ms = (time.monotonic() - reid_start) * 1000
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
                    color = (100, 220, 255) if face["status"] == "accepted" else (100, 130, 220)
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
                    return
                run.jpeg = encoded.tobytes()
                if self.events is not None and search_status == "ready":
                    self.events.submit_tracks(
                        run.camera_id,
                        frame.stream_session_id,
                        faces,
                        tracks,
                        gallery_revision,
                        time.monotonic(),
                    )
                if self.diagnostics is not None:
                    self.diagnostics.submit_tracks(
                        run.camera_id,
                        frame.stream_session_id,
                        frame,
                        tracks,
                        self.sampling_revision,
                        search_status,
                    )
                run.result = {
                    "stream_session_id": frame.stream_session_id,
                    "frame_id": frame.frame_id,
                    "captured_at": frame.captured_at,
                    "tracks": tracks,
                    "gallery_revision": gallery_revision,
                    "search_status": search_status,
                    "detection_confidence": self.settings.detection_confidence,
                }
                run.processed += 1
                run.session_processed += 1
                run.face_counts.update(face_counts)
                run.reid_counts.update(reid_counts)
                if reid_counts["roi_attempts"]:
                    run.reid_ms.append(reid_ms)
                if face_counts["analysis_frames"]:
                    run.face_ms.append(face_ms)
                    run.face_process_ms.append(face_process_ms)
                    run.face_search_ms.append(face_search_ms)
                run.max_people = max(run.max_people, len(tracks))
                run.detection_ms.append(detection_ms)
                run.latencies.append((time.monotonic() - frame.captured_mono) * 1000)
                if run.state != "draining":
                    run.state = "running"
                    run.error_code = None
        except Exception as exc:
            with run.lock:
                if run.cancel.is_set() or frame.stream_session_id != run.stream_session_id:
                    return
                logging.getLogger("cctv.worker").error(
                    "Camera %s inference failed; type=%s", run.camera_id, type(exc).__name__
                )
                run.fail("inference_failed")

    def analyze_reference(self, content):
        if self.face_analyzer is None or self.cancel.is_set():
            raise RuntimeError("Face analysis unavailable")
        future = Future()
        try:
            self.commands.put_nowait((future, content))
        except Full:
            raise OverflowError("Reference queue full") from None
        try:
            return future.result(timeout=8)
        except TimeoutError:
            future.cancel()
            raise

    def submit_face_task(self, task):
        """Video tests share the GPU thread with references and live analysis."""
        if self.face_analyzer is None or self.cancel.is_set():
            raise RuntimeError("Face analysis unavailable")
        future = Future()
        self.commands.put_nowait((future, task))
        return future

    def close(self):
        if self.face_tests:
            self.face_tests.close()
        if self.clips:
            self.clips.close()
        self.cancel.set()
        with self.lock:
            runs = list(self.runs.values())
        for run in runs:
            try:
                self.stop(run.camera_id)
            except TimeoutError:
                pass
        self.scheduler.join(timeout=10)
        self.reidentifier = None
        while not self.commands.empty():
            future, _ = self.commands.get_nowait()
            future.cancel()
        if self.gallery:
            self.gallery.close()
        if self.diagnostics:
            self.diagnostics.close()
        if self.events:
            self.events.close()
