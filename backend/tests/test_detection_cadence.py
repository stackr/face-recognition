"""Different detector rates must count source frames, including under GPU load."""

import time
from types import SimpleNamespace

import numpy as np
import pytest
from app.schemas.recognition import SamplingSettings
from app.worker.api import create_worker
from fastapi.testclient import TestClient
from test_faces import StubFaces
from test_uploaded_faces import VideoModels
from test_worker import TestDetector, wait_for, write_video


@pytest.mark.parametrize(
    "person_all,face_all,person_count,face_count",
    [(False, True, 5, 20), (True, False, 20, 2), (True, True, 20, 20)],
)
def test_uploaded_independent_rates_and_lossless_frames(
    app_context, person_all, face_all, person_count, face_count
):
    settings = app_context[2]
    settings.detection_fps = 5
    settings.face_detection_fps = 2
    settings.person_all_frames = person_all
    settings.face_all_frames = face_all
    video = settings.video_dir / "cadence.mp4"
    write_video(video, seconds=1)

    class Detector(TestDetector):
        calls = 0

        def detect(self, image):
            self.calls += 1
            return super().detect(image)

    class SlowFaces(StubFaces):
        def inspect(self, image, bbox):
            # Longer than the source's 50 ms frame interval: latest-only fails.
            time.sleep(0.065)
            return super().inspect(image, bbox)

    detector, faces = Detector(), SlowFaces()
    with TestClient(
        create_worker(settings, detector, face_analyzer=faces, enable_gallery=False)
    ) as client:
        client.headers["X-Service-Token"] = settings.service_token.get_secret_value()
        assert (
            client.post(
                "/internal/cameras/1/start",
                json={
                    "source": str(video),
                    "source_type": "mp4",
                    "loop": False,
                },
            ).status_code
            == 200
        )
        ended = wait_for(client, "/internal/cameras/1", lambda value: value["state"] == "ended")
        assert ended["processed_frames"] == ended["captured_frames"] == 20
        assert ended["dropped_frames"] == 0
        assert ended["pending_frames"] == 0
        assert ended["person_detection_frames"] == detector.calls == person_count
        assert ended["face_detection_frames"] == faces.calls == face_count
        assert faces.embeddings == face_count


def test_uploaded_direct_faces_all_frames_never_runs_person_detector(app_context):
    settings = app_context[2]
    settings.person_detection_enabled = False
    settings.face_all_frames = True
    settings.video_face_detection_threshold = 0.1
    video = settings.video_dir / "direct-cadence.mp4"
    write_video(video, seconds=1)

    class ForbiddenDetector(TestDetector):
        def detect(self, image):
            raise AssertionError("Face-only uploads must not run the person model")

    models = VideoModels()
    faces = SimpleNamespace(models=models, embed=lambda image: np.ones(512, np.float32))
    with TestClient(
        create_worker(settings, ForbiddenDetector(), face_analyzer=faces, enable_gallery=False)
    ) as client:
        client.headers["X-Service-Token"] = settings.service_token.get_secret_value()
        client.post(
            "/internal/cameras/1/start",
            json={
                "source": str(video),
                "source_type": "mp4",
                "loop": False,
            },
        )
        ended = wait_for(client, "/internal/cameras/1", lambda value: value["state"] == "ended")
        assert ended["captured_frames"] == ended["processed_frames"] == 20
        assert ended["dropped_frames"] == ended["person_detection_frames"] == 0
        assert ended["face_detection_frames"] == len(models.calls) == 20


def test_new_cadence_controls_persist_and_legacy_interval_still_updates_fps(
    app_context, admin_headers
):
    client = app_context[0]
    original = client.get("/api/function-settings").json()
    values = original["values"] | {
        "detection_fps": 5,
        "face_detection_fps": 30,
        "face_all_frames": True,
        "person_all_frames": False,
    }
    saved = client.put(
        "/api/function-settings", json=values | {"revision": 0}, headers=admin_headers
    )
    assert saved.status_code == 202
    assert client.get("/api/function-settings").json()["values"] == values
    legacy = {
        key: values[key]
        for key in (
            "detection_fps",
            "face_analysis_interval",
            "face_rois_per_frame",
            "face_match_threshold",
            "detection_confidence",
        )
    }
    legacy["face_analysis_interval"] = 0.25
    result = client.put(
        "/api/function-settings", json=legacy | {"revision": 1}, headers=admin_headers
    )
    assert result.status_code == 202
    assert result.json()["values"]["face_detection_fps"] == 4
    assert result.json()["values"]["face_all_frames"] is True
    assert result.json()["values"]["person_all_frames"] is False
    assert SamplingSettings.defaults(app_context[2]).face_detection_fps == 2
