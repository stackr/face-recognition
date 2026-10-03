import asyncio
import threading
import time
from types import SimpleNamespace

import cv2
import httpx
import numpy as np
import pytest
from app.api.analysis import preview_frames
from app.worker.api import create_worker
from app.worker.reconnect import reconnect_delay
from fastapi.testclient import TestClient
from test_analysis import replace_worker
from test_faces import StubFaces
from test_foundation import PAYLOAD
from test_worker import TestDetector, wait_for


class Capture:
    def __init__(self, *, opened=True, width=160):
        self.opened, self.width = opened, width
        self.disconnect = threading.Event()
        self.released = False

    def isOpened(self):
        return self.opened

    def get(self, key):
        return {cv2.CAP_PROP_FRAME_WIDTH: self.width, cv2.CAP_PROP_FRAME_HEIGHT: 120}.get(key, 50)

    def read(self):
        if self.disconnect.wait(0.02):
            return False, None
        return True, np.zeros((120, 160, 3), np.uint8)

    def release(self):
        self.released = True


def worker_client(settings, detector=None, faces=None):
    return TestClient(
        create_worker(
            settings,
            detector or TestDetector(),
            face_analyzer=faces,
            enable_faces=faces is not None,
            enable_gallery=False,
        )
    )


def configure(settings, *, delay=0.2, maximum=0.8):
    settings.rtsp_reconnect_initial_seconds = delay
    settings.rtsp_reconnect_max_seconds = maximum
    settings.rtsp_reconnect_jitter = 0


RTSP = {"source": "rtsp://test:unit-private@127.0.0.1:8554/smoke", "source_type": "rtsp"}


def authorize(client, settings):
    client.headers["X-Service-Token"] = settings.service_token.get_secret_value()


def test_rtsp_reconnect_clears_faces_tracks_preview_and_session(app_context, monkeypatch):
    settings = app_context[2]
    configure(settings, delay=0.4)
    first, second = Capture(), Capture()
    opened = []

    def capture(source, backend, params):
        opened.append((backend, params))
        return first if len(opened) == 1 else second

    monkeypatch.setattr(cv2, "VideoCapture", capture)
    faces = StubFaces()
    with worker_client(settings, faces=faces) as client:
        authorize(client, settings)
        client.post("/internal/cameras/1/start", json=RTSP)
        running = wait_for(
            client,
            "/internal/cameras/1",
            lambda s: (
                s["state"] == "running" and s["face_counts"].get("embeddings_created", 0) >= 1
            ),
        )
        old = running["stream_session_id"]
        path = "/internal/cameras/1/faces/1"
        assert client.get(path, params={"stream_session_id": old}).status_code == 200
        first.disconnect.set()
        waiting = wait_for(client, "/internal/cameras/1", lambda s: s["state"] == "reconnecting")
        assert waiting["stream_session_id"] != old
        assert waiting["result"] is None and waiting["face_cache_tracks"] == 0
        assert waiting["pending_frames"] == waiting["session_processed_frames"] == 0
        response = client.get("/internal/cameras/1/frame")
        assert response.status_code == 204 and response.headers["X-Camera-State"] == "reconnecting"
        assert client.get(path, params={"stream_session_id": old}).status_code == 404
        recovered = wait_for(
            client,
            "/internal/cameras/1",
            lambda s: (
                s["reconnects"] == 1
                and s["state"] == "running"
                and s["face_counts"].get("embeddings_created", 0) >= 2
                and s["dropped_frames"] > 0
            ),
        )
        assert recovered["state"] == "running" and recovered["error_code"] is None
        assert recovered["stream_session_id"] == waiting["stream_session_id"]
        assert recovered["result"]["tracks"][0]["track_id"] == 1
        assert faces.embeddings >= 2
        assert recovered["session_processed_frames"] < recovered["processed_frames"]
        assert recovered["pending_frames"] <= 1 and recovered["dropped_frames"] > 0
        assert client.get(path, params={"stream_session_id": old}).status_code == 404
        assert opened[0] == (
            cv2.CAP_FFMPEG,
            [
                cv2.CAP_PROP_N_THREADS,
                2,
                cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,
                5000,
                cv2.CAP_PROP_READ_TIMEOUT_MSEC,
                3000,
            ],
        )
        client.post("/internal/cameras/1/stop")
        assert all(cap.released for cap in (first, second))


def test_stop_interrupts_backoff_and_reconnecting_counts_toward_limit(app_context, monkeypatch):
    settings = app_context[2]
    configure(settings, delay=30, maximum=30)
    settings.max_active_cameras = 1
    attempts = []

    def capture(*args):
        attempts.append(time.monotonic())
        return Capture(opened=False)

    monkeypatch.setattr(cv2, "VideoCapture", capture)
    with worker_client(settings) as client:
        authorize(client, settings)
        client.post("/internal/cameras/1/start", json=RTSP)
        wait_for(client, "/internal/cameras/1", lambda s: s["state"] == "reconnecting")
        assert client.post("/internal/cameras/1/start", json=RTSP).status_code == 409
        assert client.post("/internal/cameras/2/start", json=RTSP).status_code == 429
        start = time.monotonic()
        assert client.post("/internal/cameras/1/stop").json()["state"] == "stopped"
        assert time.monotonic() - start < 1
        time.sleep(0.25)
        assert len(attempts) == 1
        assert client.app.state.runtime.get(1).source == ""


def test_backoff_grows_is_bounded_and_resets_after_stable_frames(app_context, monkeypatch):
    settings = app_context[2]
    configure(settings, delay=0.1, maximum=0.4)
    settings.rtsp_reconnect_reset_seconds = 1
    live = Capture()
    attempts = []

    def capture(*args):
        attempts.append(time.monotonic())
        return live if len(attempts) == 4 else Capture(opened=False)

    monkeypatch.setattr(cv2, "VideoCapture", capture)
    with worker_client(settings) as client:
        authorize(client, settings)
        client.post("/internal/cameras/1/start", json=RTSP)
        state = wait_for(
            client,
            "/internal/cameras/1",
            lambda s: s["state"] == "running" and s["consecutive_failures"] == 0,
        )
        assert state["connection_attempts"] == 4
        for duration, expected in zip(np.diff(attempts), [0.1, 0.2, 0.4], strict=True):
            assert expected <= duration < expected + 0.2
        live.disconnect.set()
        retry = wait_for(client, "/internal/cameras/1", lambda s: s["state"] == "reconnecting")
        assert retry["consecutive_failures"] == 1 and retry["next_retry_seconds"] <= 0.1
        assert reconnect_delay(settings, 10000) == 0.4
        settings.rtsp_reconnect_jitter = 0.2
        for _ in range(100):
            assert 0.32 <= reconnect_delay(settings, 10000) <= 0.4


@pytest.mark.parametrize("old_fails", [False, True])
def test_old_session_inference_cannot_publish_or_stop_new_connection(
    app_context, monkeypatch, old_fails
):
    settings = app_context[2]
    configure(settings, delay=0.1)
    first, second = Capture(), Capture()
    captures = iter([first, second])
    monkeypatch.setattr(cv2, "VideoCapture", lambda *args: next(captures))
    entered, release = threading.Event(), threading.Event()

    class BlockingDetector(TestDetector):
        def detect(self, frame):
            if not entered.is_set():
                entered.set()
                assert release.wait(4)
                if old_fails:
                    raise RuntimeError("Old frame failed")
            return super().detect(frame)

    with worker_client(settings, detector=BlockingDetector()) as client:
        authorize(client, settings)
        initial = client.post("/internal/cameras/1/start", json=RTSP).json()
        try:
            assert entered.wait(2)
            first.disconnect.set()
            connected = wait_for(client, "/internal/cameras/1", lambda s: s["reconnects"] == 1)
            assert connected["stream_session_id"] != initial["stream_session_id"]
        finally:
            release.set()
        running = wait_for(client, "/internal/cameras/1", lambda s: s["state"] == "running")
        assert running["error_code"] is None
        assert running["result"]["stream_session_id"] == connected["stream_session_id"]


def test_bad_resolution_is_terminal_and_capture_exceptions_are_sanitized(
    app_context, monkeypatch, caplog
):
    settings = app_context[2]
    configure(settings)
    calls = []

    def capture(*args):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError(RTSP["source"])
        return Capture(width=5000)

    monkeypatch.setattr(cv2, "VideoCapture", capture)
    with worker_client(settings) as client:
        authorize(client, settings)
        client.post("/internal/cameras/1/start", json=RTSP)
        waiting = wait_for(client, "/internal/cameras/1", lambda s: s["state"] == "reconnecting")
        assert waiting["error_code"] == "capture_failed"
        failed = wait_for(client, "/internal/cameras/1", lambda s: s["state"] == "error")
        assert failed["error_code"] == "source_resolution_exceeded"
        time.sleep(0.3)
        assert len(calls) == 2 and "unit-private" not in caplog.text


@pytest.mark.parametrize("changed", ["reconnecting", "new_session"])
def test_preview_ends_and_releases_viewer_on_disconnect_or_session_change(
    app_context, admin_headers, changed
):
    client = app_context[0]
    camera_id = client.post("/api/cameras", json=PAYLOAD, headers=admin_headers).json()["camera_id"]
    viewers = client.app.state.viewers
    viewers.acquire(camera_id)

    async def connected():
        return False

    request = SimpleNamespace(
        app=client.app, cookies=dict(client.cookies), method="GET", is_disconnected=connected
    )
    count = 0

    def handler(req):
        nonlocal count
        count += 1
        return httpx.Response(
            204 if count > 1 and changed == "reconnecting" else 200,
            content=b"jpeg" if count == 1 or changed == "new_session" else b"",
            headers={
                "X-Camera-State": "reconnecting"
                if count > 1 and changed == "reconnecting"
                else "running",
                "X-Stream-Session": "a" * 32 if count == 1 else "b" * 32,
                "X-Frame-Id": "1",
            },
        )

    async def consume():
        stream = preview_frames(request, camera_id, httpx.MockTransport(handler))
        assert b"jpeg" in await anext(stream)
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
        assert not viewers.counts

    asyncio.run(consume())


def test_video_upload_is_blocked_during_reconnection(app_context, admin_headers):
    client = app_context[0]
    camera_id = client.post("/api/cameras", json=PAYLOAD, headers=admin_headers).json()["camera_id"]
    replace_worker(client, lambda req: httpx.Response(200, json={"state": "reconnecting"}))
    assert (
        client.put(
            f"/api/cameras/{camera_id}/video",
            content=b"test",
            headers={**admin_headers, "Content-Type": "video/mp4"},
        ).status_code
        == 409
    )
