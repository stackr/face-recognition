"""Per-analysis detector choices must be isolated and enforced by the server."""

from types import SimpleNamespace

import numpy as np
import pytest
from app.schemas.recognition import SamplingSettings
from app.worker.api import create_worker
from app.worker.runtime import CameraRun, Frame, WorkerRuntime
from fastapi.testclient import TestClient
from test_uploaded_faces import VideoModels
from test_worker import TestDetector, boxes, wait_for, write_video


@pytest.mark.parametrize("source_type", ["mp4", "rtsp"])
def test_person_only_skips_face_embedding_gallery_events_and_face_logs(app_context, source_type):
    settings = app_context[2]
    settings.person_detection_enabled = False
    settings.face_all_frames = True

    def forbidden(*args, **kwargs):
        raise AssertionError("Face comparison and evidence must be disabled")

    analyzer = SimpleNamespace(inspect=forbidden, embed=forbidden)
    gallery = SimpleNamespace(snapshot=forbidden, close=lambda: None)
    runtime = WorkerRuntime(settings, TestDetector(), analyzer, gallery)
    runtime.events = SimpleNamespace(submit_tracks=forbidden, close=lambda: None)
    runtime.diagnostics = SimpleNamespace(submit_tracks=forbidden, close=lambda: None)
    run = CameraRun(
        1,
        "",
        source_type,
        False,
        settings,
        person_detection_enabled=True,
        face_detection_enabled=False,
    )
    frame = Frame(
        np.zeros((120, 160, 3), np.uint8), run.stream_session_id, 1, "2026-10-05T00:00:00Z", 10
    )
    try:
        assert run.person_detection_enabled and not run.face_detection_enabled
        assert not run.lossless_mp4()  # Disabled face all-frames must not slow people.
        assert runtime.frame_cadence(run, frame) == (True, False)
        runtime.process_frame(run, frame, boxes(), 0, face_due=False)
        assert run.state == "running" and run.result["tracks"]
        assert run.result["search_status"] == "disabled"
        assert run.result["face_detection_enabled"] is False
        assert all("face" not in track for track in run.result["tracks"])
        assert not run.face_counts
    finally:
        runtime.close()


def test_explicit_camera_choices_ignore_legacy_global_mode_changes(app_context):
    settings = app_context[2]
    runtime = WorkerRuntime(settings, TestDetector())
    runs = [
        CameraRun(1, "", "mp4", False, settings, person_detection_enabled=True),
        CameraRun(2, "", "mp4", False, settings, person_detection_enabled=False),
    ]
    sessions = [run.stream_session_id for run in runs]
    try:
        with runtime.lock:
            runtime.runs = {run.camera_id: run for run in runs}
            runtime.queue_sampling(
                1,
                SamplingSettings.defaults(settings).model_copy(
                    update={"person_detection_enabled": False}
                ),
            )
            runtime.apply_sampling()
            assert [run.stream_session_id for run in runs] == sessions
            assert runs[0].person_detection_enabled is True
            assert runs[1].person_detection_enabled is False
            assert runtime.direct_face_mode(runs[0])
            assert runtime.direct_face_mode(runs[1])
            runtime.runs.clear()
    finally:
        runtime.close()


def test_worker_rejects_no_detectors_and_strict_boolean_choices(app_context):
    settings = app_context[2]
    video = settings.video_dir / "selection.mp4"
    write_video(video, seconds=1)
    with TestClient(
        create_worker(settings, TestDetector(), enable_faces=False, enable_gallery=False)
    ) as client:
        client.headers["X-Service-Token"] = settings.service_token.get_secret_value()
        payload = {"source": str(video), "source_type": "mp4", "loop": False}
        for invalid in (
            {"person_detection_enabled": False, "face_detection_enabled": False},
            {"person_detection_enabled": "false"},
            {"face_detection_enabled": 0},
        ):
            assert (
                client.post("/internal/cameras/1/start", json=payload | invalid).status_code == 422
            )
        assert (
            client.post(
                "/internal/cameras/1/start",
                json=payload
                | {
                    "person_detection_enabled": True,
                    "face_detection_enabled": False,
                },
            ).status_code
            == 200
        )
        ended = wait_for(client, "/internal/cameras/1", lambda value: value["state"] == "ended")
        assert ended["person_detection_enabled"] is True
        assert ended["face_detection_enabled"] is False
        assert ended["face_detection_frames"] == 0


@pytest.mark.parametrize("source_type", ["mp4", "rtsp"])
def test_face_only_choice_overrides_person_default(app_context, source_type):
    settings = app_context[2]
    settings.person_detection_enabled = True
    settings.video_face_detection_threshold = 0.1
    analyzer = SimpleNamespace(models=VideoModels(), embed=lambda image: np.ones(512, np.float32))
    runtime = WorkerRuntime(settings, TestDetector(), analyzer)
    run = CameraRun(
        1,
        "",
        source_type,
        False,
        settings,
        person_detection_enabled=False,
        face_detection_enabled=True,
    )
    frame = Frame(
        np.zeros((120, 160, 3), np.uint8), run.stream_session_id, 1, "2026-10-05T00:00:00Z", 10
    )
    try:
        runtime.process_frame(run, frame, None, 0, face_due=True)
        assert run.result["detection_mode"] == "face" and len(run.result["tracks"]) == 2
        assert run.tracker is None and run.face_counts["embeddings_created"] == 2
    finally:
        runtime.close()


@pytest.mark.parametrize(
    "selection",
    [
        {"person_detection_enabled": False, "face_detection_enabled": False},
        {"person_detection_enabled": "false"},
        {"face_detection_enabled": "true"},
    ],
)
def test_public_start_validates_selection_before_worker(app_context, admin_headers, selection):
    client = app_context[0]
    camera = client.post(
        "/api/cameras", json={"name": "selection", "source_type": "mp4"}, headers=admin_headers
    ).json()
    assert (
        client.post(
            f"/api/cameras/{camera['camera_id']}/start", json=selection, headers=admin_headers
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    "selection",
    [
        {},
        {"face_detection_enabled": True},
        {"face_detection_enabled": False},
        {"person_detection_enabled": False, "face_detection_enabled": True},
        {"person_detection_enabled": True, "face_detection_enabled": True},
        {"person_detection_enabled": True, "face_detection_enabled": False},
    ],
)
def test_public_rtsp_start_preserves_detection_selection(app_context, admin_headers, selection):
    import json

    import httpx
    from test_analysis import replace_worker
    from test_foundation import PAYLOAD

    client, _, settings = app_context
    settings.person_detection_enabled = False
    camera_id = client.post("/api/cameras", json=PAYLOAD, headers=admin_headers).json()["camera_id"]
    commands = []

    def worker(request):
        commands.append(json.loads(request.content))
        return httpx.Response(200, json={"camera_id": camera_id, "state": "opening"})

    replace_worker(client, worker)
    assert (
        client.post(
            f"/api/cameras/{camera_id}/start", json=selection, headers=admin_headers
        ).status_code
        == 200
    )
    assert commands[-1]["person_detection_enabled"] is selection.get("person_detection_enabled")
    assert commands[-1]["face_detection_enabled"] is selection.get("face_detection_enabled", True)
    assert (
        client.post(
            f"/api/cameras/{camera_id}/start",
            json={"person_detection_enabled": False, "face_detection_enabled": False},
            headers=admin_headers,
        ).status_code
        == 422
    )
    assert len(commands) == 1


@pytest.mark.parametrize(
    "selection",
    [
        {},
        {"face_detection_enabled": True},
        {"face_detection_enabled": False},
        {"person_detection_enabled": False, "face_detection_enabled": True},
        {"person_detection_enabled": True, "face_detection_enabled": True},
        {"person_detection_enabled": True, "face_detection_enabled": False},
    ],
)
def test_worker_rtsp_start_preserves_detection_selection(app_context, selection):
    settings = app_context[2]
    settings.person_detection_enabled = False
    with TestClient(
        create_worker(settings, TestDetector(), enable_faces=False, enable_gallery=False)
    ) as client:
        client.headers["X-Service-Token"] = settings.service_token.get_secret_value()
        client.app.state.runtime.start = lambda *args, **kwargs: kwargs
        payload = {"source": "rtsp://localhost/test", "source_type": "rtsp"}
        response = client.post("/internal/cameras/1/start", json=payload | selection)
        assert response.status_code == 200
        assert response.json() == {
            "person_detection_enabled": selection.get("person_detection_enabled"),
            "face_detection_enabled": selection.get("face_detection_enabled", True),
        }
        assert (
            client.post(
                "/internal/cameras/1/start",
                json=payload | {"person_detection_enabled": False, "face_detection_enabled": False},
            ).status_code
            == 422
        )
