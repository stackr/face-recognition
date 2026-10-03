import json
from types import SimpleNamespace

import numpy as np
import pytest
from app.worker.api import create_worker
from app.worker.reid import BodyTrackCache, DisabledReIdentifier, OSNetReIdentifier, body_embedding
from fastapi.testclient import TestClient
from test_faces import StubFaces
from test_worker import TestDetector, wait_for, write_video


class StubReid(DisabledReIdentifier):
    info = {"status": "ready", "model_version": "unit-body-model", "actual_device": "cpu"}

    def __init__(self):
        self.calls = 0
        self.fail = False

    def extract_embedding(self, image):
        self.calls += 1
        if self.fail:
            raise RuntimeError("simulated model outage")
        return body_embedding(np.ones(512, np.float32))


def frame(second, session="a" * 32):
    return SimpleNamespace(
        image=np.zeros((160, 160, 3), np.uint8),
        stream_session_id=session,
        captured_mono=second,
        frame_id=round(second * 10) + 1,
        captured_at="2026-10-03T00:00:00Z",
    )


def test_cosine_normalization_zero_nan_dimension_and_disabled():
    model = DisabledReIdentifier()
    a = np.ones(512, np.float32)
    assert model.compare(a, a * 2) == pytest.approx(1)
    assert model.compare(a, -a) == pytest.approx(-1)
    for bad in [np.zeros(512), np.ones(511), np.full(512, np.nan)]:
        with pytest.raises(ValueError):
            model.compare(a, bad)
    with pytest.raises(RuntimeError, match="disabled"):
        model.extract_embedding(np.zeros((256, 128, 3), np.uint8))


def test_body_cache_roi_budget_capacity_interval_session_and_metadata_only(app_context):
    settings = app_context[2].model_copy(
        update={"reid_rois_per_frame": 1, "reid_tracks_per_camera": 1}
    )
    cache, model = BodyTrackCache(settings), StubReid()
    tracks = [{"track_id": i, "bbox": [10, 10, 100, 150]} for i in (1, 2)]
    counts = cache.process(model, frame(0), tracks, {1, 2})
    assert counts["embeddings_created"] == 1 and cache.size() == 1
    assert tracks[0]["reid"]["embedding_ready"] and tracks[1]["reid"]["status"] == "capacity"
    assert "embedding" not in tracks[0]["reid"] and "vector" not in json.dumps(tracks)
    assert cache.process(model, frame(1), tracks, {1, 2})["roi_attempts"] == 0
    cache.process(model, frame(2, "b" * 32), tracks, {1})
    assert model.calls == 2 and cache.tracks[1]["session_id"] == "b" * 32
    cache.process(model, frame(6, "b" * 32), [], set())
    assert cache.size() == 0


def test_model_failure_and_small_body_clear_previous_embedding(app_context):
    cache, model = BodyTrackCache(app_context[2]), StubReid()
    tracks = [{"track_id": 1, "bbox": [0, 0, 100, 150]}]
    cache.process(model, frame(0), tracks, {1})
    assert cache.tracks[1]["embedding"] is not None
    model.fail = True
    counts = cache.process(model, frame(2), tracks, {1})
    assert counts["failures"] == 1 and tracks[0]["reid"]["status"] == "unavailable"
    assert cache.tracks[1]["embedding"] is None
    tracks[0]["bbox"] = [0, 0, 10, 10]
    assert cache.process(model, frame(4), tracks, {1})["small_body"] == 1


def test_reid_weights_fail_checksum_before_loading_checkpoint(app_context, tmp_path):
    path = tmp_path / "bad.pth"
    path.write_bytes(b"not a trusted checkpoint")
    with pytest.raises(ValueError, match="checksum"):
        OSNetReIdentifier(app_context[2].model_copy(update={"reid_model_path": path}))


@pytest.mark.parametrize("available", [True, False])
def test_optional_worker_ready_or_unavailable_keeps_detection_and_stop(app_context, available):
    settings = app_context[2].model_copy(
        update={
            "reid_enabled": True,
            "reid_model_path": app_context[2].video_dir / "missing-model.pth",
        }
    )
    source = settings.video_dir / "body-test.mp4"
    write_video(source)
    token = {"X-Service-Token": settings.service_token.get_secret_value()}
    model = StubReid() if available else None
    app = create_worker(
        settings,
        TestDetector(),
        face_analyzer=StubFaces(),
        reidentifier=model,
        enable_faces=True,
        enable_gallery=False,
    )
    with TestClient(app) as client:
        client.headers.update(token)
        info = client.get("/internal/status", headers=token).json()["person_reid"]
        assert info["status"] == ("ready" if available else "unavailable")
        assert (
            client.post(
                "/internal/cameras/1/start",
                headers=token,
                json={"source": str(source), "source_type": "mp4", "loop": True},
            ).status_code
            == 200
        )
        status = wait_for(client, "/internal/cameras/1", lambda s: s["processed_frames"] >= 2)
        assert status["face_counts"]["embeddings_created"] >= 1
        if available:
            assert status["reid_counts"]["embeddings_created"] >= 1
            metadata = status["result"]["tracks"][0]["reid"]
            assert metadata["embedding_ready"] and not metadata["identity_assignment"]
            assert "embedding" not in metadata and "vector" not in metadata
        else:
            assert not status["reid_counts"] and status["reid_cache_tracks"] == 0
        stopped = client.post("/internal/cameras/1/stop", headers=token).json()
        assert stopped["reid_cache_tracks"] == 0 and stopped["result"] is None


def test_stop_during_face_work_cannot_recreate_private_body_cache(app_context):
    import threading
    from collections import Counter

    from app.worker.runtime import WorkerRuntime

    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    class WaitingFaces(StubFaces):
        def inspect(self, image, bbox):
            entered.set()
            assert release.wait(3)
            return super().inspect(image, bbox)

    settings = app_context[2]
    source = settings.video_dir / "body-stop.mp4"
    write_video(source)
    runtime = WorkerRuntime(settings, TestDetector(), WaitingFaces())
    runtime.reidentifier = StubReid()
    original = runtime.process_frame

    def observed(*args):
        try:
            original(*args)
        finally:
            finished.set()

    runtime.process_frame = observed
    try:
        runtime.start(1, str(source), "mp4", True)
        assert entered.wait(3)
        stopped = runtime.stop(1)
        assert stopped["reid_cache_tracks"] == 0
        release.set()
        assert finished.wait(3)
        run = runtime.get(1)
        assert run.reid is None and run.result is None and run.reid_counts == Counter()
    finally:
        release.set()
        runtime.close()
