"""Body crops and full-frame face detections are separate, private session data."""

import time
from types import SimpleNamespace

import cv2
import numpy as np
from app.schemas.recognition import SamplingSettings
from app.worker.api import create_worker
from app.worker.runtime import CameraRun, Frame, WorkerRuntime
from fastapi.testclient import TestClient
from test_uploaded_faces import VideoModels
from test_worker import TestDetector, boxes, wait_for, write_video


def test_uploaded_faces_survive_missing_people_and_body_crops_are_separate(app_context):
    settings = app_context[2]
    settings.video_face_detection_threshold = 0.1
    models = VideoModels()
    analyzer = SimpleNamespace(models=models, info={}, embed=lambda image: np.ones(512, np.float32))
    runtime = WorkerRuntime(settings, TestDetector(), analyzer)
    run = CameraRun(
        1, "", "mp4", False, settings, person_detection_enabled=True, face_detection_enabled=True
    )
    image = np.full((120, 160, 3), (80, 140, 180), np.uint8)
    try:
        first = Frame(image, run.stream_session_id, 1, "2026-10-05T00:00:00Z", time.monotonic())
        runtime.process_frame(run, first, boxes(empty=True), 0, face_due=True)
        assert run.result["independent_detection"]
        assert run.result["person_tracks"] == []
        assert len(run.result["tracks"]) == 2  # Face model is not gated by people.
        second = Frame(image, run.stream_session_id, 2, "2026-10-05T00:00:01Z", time.monotonic())
        runtime.process_frame(run, second, boxes(), 0, face_due=True)
        # ByteTrack confirms a new arrival on its next detector observation.
        third = Frame(
            image, run.stream_session_id, 3, "2026-10-05T00:00:02Z", time.monotonic() + 0.2
        )
        runtime.process_frame(run, third, boxes(), 0, face_due=True)
        assert len(run.result["person_tracks"]) == 1 and len(run.result["tracks"]) == 2
        person = run.result["person_tracks"][0]
        assert person["person_image"]["frame_id"] == 3 and "face" not in person
        jpeg = run.person_images[person["track_id"]][0]
        crop = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        assert crop.shape[:2] == (80, 60)
        assert np.max(np.abs(crop.astype(int) - image[20:100, 20:80].astype(int))) < 4
        with runtime.lock:
            runtime.runs[1] = run
            runtime.queue_sampling(
                1,
                SamplingSettings.defaults(settings).model_copy(update={"video_face_min_size": 40}),
            )
            runtime.apply_sampling()
            assert (
                run.faces is None
            )  # Criteria also invalidate independent faces with people enabled.
            assert person["track_id"] in run.person_images
            runtime.runs.clear()
        with run.lock:
            run.clear_session(rotate=True)
        assert run.person_images == {}
    finally:
        with runtime.lock:
            runtime.runs.clear()
        runtime.close()


def test_person_images_without_faces_expire_on_loop_stop_and_eof(app_context):
    settings = app_context[2]
    source = settings.video_dir / "people-crops.mp4"
    write_video(source, seconds=1)
    worker = create_worker(settings, TestDetector(), enable_faces=False, enable_gallery=False)
    payload = {
        "source": str(source),
        "source_type": "mp4",
        "loop": True,
        "person_detection_enabled": True,
        "face_detection_enabled": False,
    }
    with TestClient(worker) as client:
        client.headers["X-Service-Token"] = settings.service_token.get_secret_value()
        assert client.post("/internal/cameras/1/start", json=payload).status_code == 200
        state = wait_for(
            client, "/internal/cameras/1", lambda value: value.get("result") is not None
        )
        session = state["stream_session_id"]
        track = state["result"]["person_tracks"][0]
        path = f"/internal/cameras/1/people/{track['track_id']}"
        response = client.get(path, params={"stream_session_id": session})
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert cv2.imdecode(np.frombuffer(response.content, np.uint8), cv2.IMREAD_COLOR).shape[
            :2
        ] == (80, 60)
        assert client.get(path, params={"stream_session_id": "f" * 32}).status_code == 404
        wait_for(client, "/internal/cameras/1", lambda value: value["stream_session_id"] != session)
        assert client.get(path, params={"stream_session_id": session}).status_code == 404
        client.post("/internal/cameras/1/stop")
        assert worker.state.runtime.get(1).person_images == {}
        assert client.get(path, params={"stream_session_id": session}).status_code == 404
        client.post("/internal/cameras/1/start", json=payload | {"loop": False})
        ended = wait_for(client, "/internal/cameras/1", lambda value: value["state"] == "ended")
        assert worker.state.runtime.get(1).person_images == {}
        assert (
            client.get(path, params={"stream_session_id": ended["stream_session_id"]}).status_code
            == 404
        )
