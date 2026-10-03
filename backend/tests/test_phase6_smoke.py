"""Exercise the native smoke script with isolated HTTP/WS fixtures, without CUDA or sockets."""

import importlib.util
import io
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image


@pytest.fixture
def smoke(monkeypatch, tmp_path):
    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root / "scripts"))
    spec = importlib.util.spec_from_file_location("phase6_smoke", root / "scripts/check_phase6.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(
        module,
        "Settings",
        lambda: SimpleNamespace(max_active_cameras=2, allowed_origins=["http://localhost:4200"]),
    )
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    return module


@pytest.fixture
def scenario(smoke, monkeypatch, tmp_path):
    photo = tmp_path / "data/calibration/person_b_reference.jpg"
    video = tmp_path / "data/videos/face-smoke.mp4"
    photo.parent.mkdir(parents=True)
    video.parent.mkdir(parents=True)
    buffer = io.BytesIO()
    Image.new("RGB", (112, 112)).save(buffer, format="JPEG")
    photo.write_bytes(buffer.getvalue())
    video.write_bytes(b"fixture-video")
    worker = {
        "detector": {"actual_device": "cuda:0"},
        "face_analysis": {"actual_device": "cuda"},
        "cameras": [],
    }
    first = {
        "type": "person_match",
        "event_id": 1,
        "change_id": 1,
        "camera_id": 41,
        "person_id": 51,
        "stream_session_id": "1" * 32,
        "track_id": 1,
        "status": "candidate",
        "thumbnail_url": "/api/events/1/face",
        "frame_url": "/api/events/1/frame",
    }
    current = first.copy()
    starts, mutations = [], []

    def handle(request):
        path = request.url.path
        if request.method == "GET":
            if path == "/api/system/status":
                return httpx.Response(200, json={"phase": 6, "worker": worker})
            if path == "/api/events":
                latest = request.url.params.get("latest") == "true"
                return httpx.Response(
                    200,
                    json={
                        "items": [] if latest else [current.copy()],
                        "next_cursor": 0 if latest else 1,
                        "has_more": False,
                    },
                )
            if path in {first["thumbnail_url"], first["frame_url"]}:
                return httpx.Response(
                    200 if request.headers.get("Cookie") else 401,
                    content=photo.read_bytes(),
                )
        mutations.append((request.method, path))
        if request.method == "POST":
            if path == "/api/persons":
                return httpx.Response(201, json={"id": 51})
            if path == "/api/persons/51/faces":
                return httpx.Response(201, json={"state": "ready"})
            if path == "/api/cameras":
                return httpx.Response(201, json={"camera_id": 41})
            if path == "/api/cameras/41/start":
                starts.append(path)
                return httpx.Response(200, json={"stream_session_id": str(len(starts)) * 32})
            if path == "/api/cameras/41/stop":
                return httpx.Response(200, json={"state": "stopped"})
            if path == "/api/events/1/reject":
                current.update(status="rejected", change_id=2)
                return httpx.Response(200, json=current.copy())
            if path == "/api/events/1/confirm":
                current.update(status="confirmed", change_id=3)
                return httpx.Response(200, json=current.copy())
        if request.method == "PUT" and path == "/api/cameras/41/video":
            return httpx.Response(200, json={"state": "uploaded"})
        if request.method == "DELETE" and path in {"/api/cameras/41", "/api/persons/51"}:
            return httpx.Response(204)
        raise AssertionError("Unexpected smoke fixture request")

    real_client = httpx.Client
    transport = httpx.MockTransport(handle)
    client = real_client(base_url="http://127.0.0.1:8000", transport=transport)
    client.cookies.set(smoke.COOKIE_NAME, "private-test-cookie")

    @contextmanager
    def session():
        with client:
            yield client

    monkeypatch.setattr(smoke, "api_session", session)
    monkeypatch.setattr(
        smoke.httpx, "Client", lambda **kwargs: real_client(transport=transport, **kwargs)
    )
    connections = [
        [{"type": "ready"}, first],
        [
            {"type": "ready"},
            first | {"status": "confirmed", "change_id": 3},
            first | {"event_id": 2, "change_id": 4, "stream_session_id": "2" * 32},
        ],
    ]

    class Socket:
        def __init__(self, messages):
            self.messages = iter(messages)

        def recv(self, timeout):
            return json.dumps(next(self.messages))

    @contextmanager
    def connect(*args, **kwargs):
        yield Socket(connections.pop(0))

    monkeypatch.setattr(smoke, "connect", connect)
    return SimpleNamespace(worker=worker, mutations=mutations, photo=photo, video=video)


def read_report(smoke):
    return json.loads((smoke.ROOT / "data/reports/phase6-native.json").read_text())


@pytest.mark.parametrize("device", ["cuda:0", "cuda", "cuda:1"])
def test_cuda_device_names_reach_all_smoke_checks(smoke, scenario, device):
    scenario.worker["detector"]["actual_device"] = device
    assert smoke.main() == 0
    report = read_report(smoke)
    assert report["status"] == "passed"
    assert report["detector_device"] == device
    assert len(report["checks"]) == 8
    assert {path for method, path in scenario.mutations if method == "DELETE"} == {
        "/api/cameras/41",
        "/api/persons/51",
    }


@pytest.mark.parametrize(
    ("detector", "face", "code"),
    [
        ("cpu", "cuda", "detector_cuda_required"),
        ("cuda-extra", "cuda", "detector_cuda_required"),
        ("cuda:0", "cpu", "face_cuda_required"),
    ],
)
def test_cpu_and_invalid_devices_stop_before_creating_records(
    smoke, scenario, detector, face, code
):
    scenario.worker["detector"]["actual_device"] = detector
    scenario.worker["face_analysis"]["actual_device"] = face
    assert smoke.main() == 1
    report = read_report(smoke)
    assert report["status"] == "unavailable"
    assert report["failed_stage"] == "cuda_devices"
    assert report["error_code"] == code
    assert scenario.mutations == []


@pytest.mark.parametrize(
    ("asset", "code"), [("photo", "reference_photo_missing"), ("video", "smoke_video_missing")]
)
def test_missing_assets_report_the_specific_preflight_failure(smoke, scenario, asset, code):
    getattr(scenario, asset).unlink()
    assert smoke.main() == 1
    report = read_report(smoke)
    assert report["failed_stage"] == "sample_assets"
    assert report["error_code"] == code
    assert report["status"] == "unavailable"
    assert scenario.mutations == []


def test_first_websocket_failure_is_failed_and_removes_owned_records(smoke, scenario, monkeypatch):
    @contextmanager
    def rejected(*args, **kwargs):
        raise smoke.SmokeCheckFailure("websocket_ready_required")
        yield

    monkeypatch.setattr(smoke, "connect", rejected)
    assert smoke.main() == 1
    report = read_report(smoke)
    assert report["status"] == "failed"
    assert report["checks"] == []
    assert report["failed_stage"] == "websocket_handshake"
    assert report["error_code"] == "websocket_ready_required"
    assert {path for method, path in scenario.mutations if method == "DELETE"} == {
        "/api/cameras/41",
        "/api/persons/51",
    }


def test_login_failure_does_not_print_exception_secrets(smoke, monkeypatch, capsys):
    @contextmanager
    def rejected():
        raise httpx.ConnectError("sensitive-test-password")
        yield

    monkeypatch.setattr(smoke, "api_session", rejected)
    assert smoke.main() == 1
    report = read_report(smoke)
    assert report["status"] == "unavailable"
    assert report["error_type"] == "ConnectError"
    assert report["failed_stage"] == "login"
    assert "sensitive-test-password" not in json.dumps(report)
    assert "sensitive-test-password" not in capsys.readouterr().out
