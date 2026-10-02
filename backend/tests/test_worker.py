import os
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from app.worker.api import create_worker
from app.worker.tracker import CameraTracker
from fastapi.testclient import TestClient

os.environ.setdefault(
    "YOLO_CONFIG_DIR", str(Path(__file__).resolve().parents[2] / "data/ultralytics")
)
os.environ["YOLO_AUTOINSTALL"] = "false"


def boxes(confidence=0.9, *, empty=False):
    from ultralytics.engine.results import Boxes

    data = (
        np.empty((0, 6), dtype=np.float32)
        if empty
        else np.array([[20, 20, 80, 100, confidence, 0]], dtype=np.float32)
    )
    return Boxes(data, orig_shape=(120, 160))


def test_bytetrack_preserves_low_scores_and_camera_local_ids(app_context):
    settings = app_context[2]
    frame = np.zeros((120, 160, 3), dtype=np.uint8)
    first = CameraTracker(settings)
    second = CameraTracker(settings)
    assert first.update(boxes(), frame, 10)[0]["track_id"] == 1
    assert second.update(boxes(), frame, 10)[0]["track_id"] == 1
    result = first.update(boxes(0.3), frame, 10.2)
    assert result[0]["track_id"] == 1 and result[0]["confidence"] == 0.3
    assert second.update(boxes(), frame, 10.2)[0]["track_id"] == 1
    # Even without intervening updates, a stalled stream cannot revive old IDs.
    first.update(boxes(), frame, 14)
    assert first.update(boxes(), frame, 14.2)[0]["track_id"] == 2
    assert CameraTracker(settings).update(boxes(), frame, 15)[0]["track_id"] == 1


def write_video(path, seconds=2):
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 20, (160, 120))
    assert writer.isOpened()
    for _ in range(seconds * 20):
        writer.write(np.zeros((120, 160, 3), dtype=np.uint8))
    writer.release()


class TestDetector:
    __test__ = False
    info = {"actual_device": "cpu", "model": "unit-test-double"}

    def detect(self, frame):
        return boxes()

    def resources(self):
        return {}


def wait_for(client, path, condition, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get(path).json()
        if condition(status):
            return status
        time.sleep(0.02)
    pytest.fail("Worker did not reach the expected state")


def test_worker_auth_source_validation_latest_frame_and_restart(app_context):
    settings = app_context[2]
    settings.max_active_cameras = 1
    video = settings.video_dir / "test.mp4"
    write_video(video)
    worker = create_worker(settings, TestDetector())
    with TestClient(worker) as client:
        assert client.get("/internal/status").status_code == 401
        client.headers["X-Service-Token"] = settings.service_token.get_secret_value()
        assert client.get("/internal/status").status_code == 200
        invalid = {"source": "/etc/private-secret.mp4", "source_type": "mp4"}
        response = client.post("/internal/cameras/1/start", json=invalid)
        assert response.status_code == 422 and "private-secret" not in response.text
        payload = {"source": str(video), "source_type": "mp4", "loop": True}
        assert client.post("/internal/videos/probe", json=payload).status_code == 200
        assert client.post("/internal/cameras/1/start", json=payload).status_code == 200
        assert client.post("/internal/cameras/1/start", json=payload).status_code == 409
        assert client.post("/internal/cameras/2/start", json=payload).status_code == 429
        status = wait_for(
            client, "/internal/cameras/1", lambda value: value["processed_frames"] >= 3
        )
        assert status["pending_frames"] <= 1 and status["dropped_frames"] > 0
        assert status["max_people"] == 1
        session = status["stream_session_id"]
        frame = client.get("/internal/cameras/1/frame")
        assert frame.content[:2] == b"\xff\xd8" and frame.headers["X-Stream-Session"] == session
        assert frame.headers["X-Frame-Id"] and frame.headers["X-Captured-At"]
        assert client.post("/internal/cameras/1/stop").json()["state"] == "stopped"
        assert client.get("/internal/cameras/1/frame").status_code == 204
        restarted = client.post("/internal/cameras/1/start", json=payload).json()
        assert restarted["stream_session_id"] != session
        assert restarted["processed_frames"] == 0
        assert client.post("/internal/cameras/1/stop").status_code == 200


def test_mp4_loop_creates_new_session_and_nonloop_ends(app_context):
    settings = app_context[2]
    video = settings.video_dir / "short.mp4"
    write_video(video, seconds=1)
    with TestClient(create_worker(settings, TestDetector())) as client:
        client.headers["X-Service-Token"] = settings.service_token.get_secret_value()
        payload = {"source": str(video), "source_type": "mp4", "loop": True}
        initial = client.post("/internal/cameras/7/start", json=payload).json()
        looped = wait_for(
            client,
            "/internal/cameras/7",
            lambda value: value["loops"] >= 1 and value["result"] is not None,
        )
        assert initial["stream_session_id"] != looped["stream_session_id"]
        assert looped["result"]["tracks"][0]["track_id"] == 1
        client.post("/internal/cameras/7/stop")
        client.post("/internal/cameras/7/start", json={**payload, "loop": False})
        ended = wait_for(client, "/internal/cameras/7", lambda value: value["state"] == "ended")
        assert ended["processed_frames"] > 0
        assert client.get("/internal/cameras/7/frame").status_code == 204
