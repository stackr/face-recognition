import time
from types import SimpleNamespace

import numpy as np
import pytest
from app.models import FunctionSettings
from app.schemas.recognition import SamplingSettings
from app.worker.face_onnx import TEMPLATE
from app.worker.runtime import CameraRun, Frame, WorkerRuntime
from app.worker.uploaded_faces import FaceBoxTracker
from sqlalchemy.orm import Session
from test_worker import TestDetector


class VideoModels:
    def __init__(self):
        self.calls = []

    def detect(self, image, *, side, max_faces, score_threshold):
        self.calls.append((side, max_faces, score_threshold))
        return [
            {
                "bbox": np.array([offset, 10, offset + 65, 80], dtype=float),
                "landmarks": TEMPLATE * 0.5 + [offset, 10],
                "confidence": 0.2,
            }
            for offset in (10, 85)
            if score_threshold <= 0.2
        ]


def frame(run, number):
    return Frame(
        np.zeros((120, 160, 3), np.uint8),
        run.stream_session_id,
        number,
        "2026-10-04T00:00:00Z",
        time.monotonic() + number * 0.3,
    )


def test_legacy_update_preserves_new_video_controls(app_context, admin_headers):
    client, engine, settings = app_context
    saved = SamplingSettings.defaults(settings).model_dump() | {
        "person_detection_enabled": False,
        "video_face_detection_threshold": 0.8,
        "video_face_min_size": 40,
    }
    with Session(engine) as db:
        db.add(FunctionSettings(id=1, revision=3, values=saved))
        db.commit()
    legacy = {
        key: saved[key]
        for key in (
            "detection_fps",
            "face_analysis_interval",
            "face_rois_per_frame",
            "face_match_threshold",
            "detection_confidence",
        )
    }
    result = client.put(
        "/api/function-settings", json=legacy | {"revision": 3}, headers=admin_headers
    )
    assert result.status_code == 202 and result.json()["values"] == saved


@pytest.mark.parametrize("source_type", ["mp4", "rtsp"])
def test_direct_faces_compare_without_person_roi_or_strict_live_quality(app_context, source_type):
    settings = app_context[2]
    settings.person_detection_enabled = False
    settings.video_face_detection_threshold = 0.1
    settings.face_analysis_interval = 0.2
    models = VideoModels()
    analyzer = SimpleNamespace(models=models, embed=lambda image: np.ones(512, np.float32))
    gallery = SimpleNamespace(
        snapshot=lambda: ((1, ()), []),
        search_snapshot=lambda *args, **kwargs: {
            "matches": [{"person_id": 7, "name": "test", "face_id": 1, "similarity": 0.9}]
        },
        close=lambda: None,
    )
    runtime = WorkerRuntime(settings, TestDetector(), analyzer, gallery)
    run = CameraRun(1, "", source_type, False, settings, person_detection_enabled=False)
    try:
        for number in (1, 2):
            runtime.process_frame(run, frame(run, number), None, 0)
        assert run.result["detection_mode"] == "face" and run.state == "running"
        assert run.tracker is None and len(run.result["tracks"]) == 2
        assert [track["track_id"] for track in run.result["tracks"]] == [1, 2]
        for track in run.result["tracks"]:
            assert track["face"]["embedding_ready"]
            assert track["face"]["matches"][0]["supporting_samples"] == 2
            assert track["face"]["matches"][0]["person_id"] == 7
            assert (
                run.faces.thumbnail(track["track_id"], run.stream_session_id, time.monotonic())[:2]
                == b"\xff\xd8"
            )
        assert models.calls == [(640, None, 0.1)] * 2
        settings.video_face_min_size = 100
        runtime.process_frame(run, frame(run, 3), None, 0)
        assert run.result["tracks"] == [] and run.result["min_face_size"] == 100
        settings.video_face_min_size = 8
        settings.video_face_detection_threshold = 0.8
        runtime.process_frame(run, frame(run, 4), None, 0)
        assert run.result["tracks"] == [] and models.calls[-1][2] == 0.8
        assert runtime.direct_face_mode(CameraRun(2, "", "rtsp", False, settings))
    finally:
        runtime.close()


def test_scheduler_never_calls_yolo_for_direct_uploaded_faces(app_context):
    class ForbiddenPersonDetector(TestDetector):
        def detect(self, image):
            raise AssertionError("Uploaded face mode must not call YOLO")

    settings = app_context[2]
    settings.person_detection_enabled = False
    settings.video_face_detection_threshold = 0.1
    analyzer = SimpleNamespace(models=VideoModels(), embed=lambda image: np.ones(512, np.float32))
    runtime = WorkerRuntime(settings, ForbiddenPersonDetector(), analyzer)
    run = CameraRun(1, "", "mp4", False, settings)
    try:
        run.queue_frame(np.zeros((120, 160, 3), np.uint8))
        with runtime.lock:
            runtime.runs[1] = run
        deadline = time.monotonic() + 3
        while not run.processed and run.state != "failed" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert run.state == "running" and run.processed == 1
        assert len(run.result["tracks"]) == 2 and not runtime.batch_counts
    finally:
        with runtime.lock:
            runtime.runs.clear()
        runtime.close()


def test_mode_switch_rotates_only_uploaded_session_and_drops_faces(app_context):
    settings = app_context[2]
    runtime = WorkerRuntime(settings, TestDetector())
    runs = [CameraRun(1, "", "mp4", False, settings), CameraRun(2, "", "rtsp", False, settings)]
    sessions = [run.stream_session_id for run in runs]
    try:
        with runtime.lock:
            runtime.runs = {run.camera_id: run for run in runs}
            runtime.pending_sampling = (
                1,
                SamplingSettings.defaults(settings).model_copy(
                    update={"person_detection_enabled": False}
                ),
            )
            runs[0].faces = object()
            runtime.apply_sampling()
            assert runs[0].stream_session_id != sessions[0] and runs[0].faces is None
            assert runs[1].stream_session_id == sessions[1]
            runtime.runs.clear()
    finally:
        runtime.close()


def test_face_spatial_ids_are_one_to_one_bounded_and_expire(app_context):
    settings = app_context[2]
    settings.face_tracks_per_camera = 2
    tracker = FaceBoxTracker(settings)
    faces = VideoModels().detect(None, side=640, max_faces=None, score_threshold=0.1)
    first, _ = tracker.update(faces, 10)
    second, _ = tracker.update(list(reversed(faces)), 10.2)
    assert [track["track_id"] for track in first] == [1, 2]
    assert [track["track_id"] for track in second] == [2, 1]
    assert len(tracker.seen) == 2
    third, _ = tracker.update(faces, 14)
    assert [track["track_id"] for track in third] == [3, 4]
    assert tracker.live_ids() == {3, 4}
