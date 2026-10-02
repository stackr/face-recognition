import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from app.api.analysis import preview_access, preview_frames
from app.core.security import password_hasher
from app.models import AuditLog, Camera, User
from conftest import TEST_PASSWORD
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_foundation import PAYLOAD


def replace_worker(client, handler):
    worker = client.app.state.worker
    headers = dict(worker.client.headers)
    worker.client.close()
    worker.client = httpx.Client(
        base_url="http://127.0.0.1:8001", transport=httpx.MockTransport(handler), headers=headers
    )


def test_analysis_permissions_commands_and_secret_boundary(app_context, admin_headers):
    client, engine, _ = app_context
    camera_id = client.post("/api/cameras", json=PAYLOAD, headers=admin_headers).json()["camera_id"]
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"camera_id": camera_id, "state": "opening"})

    replace_worker(client, handler)
    assert client.post(f"/api/cameras/{camera_id}/start", json={}).status_code == 403
    response = client.post(f"/api/cameras/{camera_id}/start", json={}, headers=admin_headers)
    assert response.status_code == 200 and response.json()["state"] == "opening"
    assert "private-password" not in response.text
    command = json.loads(calls[-1].content)
    assert calls[-1].headers["X-Service-Token"] == app_context[2].service_token.get_secret_value()
    assert command["source"] == PAYLOAD["rtsp_url"]
    assert client.get(f"/api/cameras/{camera_id}/status").status_code == 200
    assert (
        client.put(
            f"/api/cameras/{camera_id}/access",
            json={"username": "viewer", "can_view": True},
            headers=admin_headers,
        ).status_code
        == 200
    )
    login = client.post("/api/auth/login", json={"username": "viewer", "password": TEST_PASSWORD})
    headers = {"X-CSRF-Token": login.json()["csrf_token"]}
    assert client.get(f"/api/cameras/{camera_id}/status").status_code == 200
    assert (
        client.post(f"/api/cameras/{camera_id}/start", json={}, headers=headers).status_code == 403
    )
    assert client.post(f"/api/cameras/{camera_id}/stop", headers=headers).status_code == 403
    with Session(engine) as db:
        assert db.scalars(
            select(AuditLog.action).where(AuditLog.action == "camera.start")
        ).all() == ["camera.start"]


def test_worker_failure_cannot_claim_success_or_delete_camera(app_context, admin_headers):
    client, engine, _ = app_context
    camera_id = client.post("/api/cameras", json=PAYLOAD, headers=admin_headers).json()["camera_id"]
    replace_worker(client, lambda request: httpx.Response(503, text="rtsp://private-password@host"))
    assert (
        client.post(f"/api/cameras/{camera_id}/start", json={}, headers=admin_headers).status_code
        == 503
    )
    response = client.delete(f"/api/cameras/{camera_id}", headers=admin_headers)
    assert response.status_code == 503 and "private-password" not in response.text
    with Session(engine) as db:
        assert db.get(Camera, camera_id) is not None
        assert db.scalar(select(AuditLog).where(AuditLog.action == "camera.start")) is None


def test_mp4_upload_is_private_bounded_and_preserves_rtsp(app_context, admin_headers):
    client, engine, settings = app_context
    camera_id = client.post("/api/cameras", json=PAYLOAD, headers=admin_headers).json()["camera_id"]
    replace_worker(
        client,
        lambda request: (
            httpx.Response(404)
            if request.method == "GET"
            else httpx.Response(200, json={"resolution": [320, 240]})
        ),
    )
    settings.video_upload_max_mb = 1
    path = f"/api/cameras/{camera_id}/video"
    assert client.put(path, content=b"video", headers=admin_headers).status_code == 415
    assert (
        client.put(
            path, content=b"x" * (2**20 + 1), headers={**admin_headers, "Content-Type": "video/mp4"}
        ).status_code
        == 413
    )
    response = client.put(
        path, content=b"unit-test-mp4", headers={**admin_headers, "Content-Type": "video/mp4"}
    )
    assert response.status_code == 200 and response.json()["has_test_video"]
    assert response.json()["source_type"] == "rtsp" and "video_path" not in response.text
    with Session(engine) as db:
        stored = db.get(Camera, camera_id)
        assert (settings.video_dir / stored.video_path).read_bytes() == b"unit-test-mp4"
        assert stored.video_path not in response.text
        encrypted = stored.rtsp_url_encrypted
    replace_worker(client, lambda request: httpx.Response(422))
    assert (
        client.put(
            path, content=b"invalid-video", headers={**admin_headers, "Content-Type": "video/mp4"}
        ).status_code
        == 422
    )
    assert len(list(settings.video_dir.glob("*.mp4"))) == 1
    with Session(engine) as db:
        assert db.get(Camera, camera_id).rtsp_url_encrypted == encrypted
    replace_worker(client, lambda request: httpx.Response(404))
    assert client.delete(f"/api/cameras/{camera_id}", headers=admin_headers).status_code == 204
    assert not list(settings.video_dir.glob("*.mp4"))


def test_mp4_source_requires_upload_and_disabled_cameras_cannot_start(client, admin_headers):
    camera = client.post(
        "/api/cameras", json={"name": "MP4", "source_type": "mp4"}, headers=admin_headers
    ).json()
    camera_id = camera["camera_id"]
    assert camera["rtsp_url"] == ""
    assert (
        client.post(f"/api/cameras/{camera_id}/start", json={}, headers=admin_headers).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/cameras/{camera_id}/start", json={"source_type": "rtsp"}, headers=admin_headers
        ).status_code
        == 409
    )
    client.put(
        f"/api/cameras/{camera_id}",
        json={"name": "MP4", "source_type": "mp4", "enabled": False},
        headers=admin_headers,
    )
    assert (
        client.post(f"/api/cameras/{camera_id}/start", json={}, headers=admin_headers).status_code
        == 409
    )


def test_preview_authentication_limits_disconnect_and_session_recheck(app_context, admin_headers):
    client, _, _ = app_context
    camera_id = client.post("/api/cameras", json=PAYLOAD, headers=admin_headers).json()["camera_id"]
    assert client.get(f"/api/cameras/{camera_id}/preview").status_code == 409
    viewers = client.app.state.viewers
    for _ in range(4):
        viewers.acquire(camera_id)
    with pytest.raises(HTTPException) as limited:
        viewers.acquire(camera_id)
    assert limited.value.status_code == 429
    for _ in range(4):
        viewers.release(camera_id)
    request = SimpleNamespace(app=client.app, cookies=dict(client.cookies), method="GET")

    async def disconnected():
        return False

    request.is_disconnected = disconnected
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200,
            content=b"\xff\xd8testjpeg",
            headers={
                "X-Stream-Session": "one-session",
                "X-Frame-Id": "1",
                "X-Captured-At": "2026-10-02T00:00:00Z",
            },
        )
    )

    async def consume():
        viewers.acquire(camera_id)
        stream = preview_frames(request, camera_id, transport)
        chunk = await anext(stream)
        assert b"X-Stream-Session: one-session" in chunk and b"\xff\xd8" in chunk
        client.post("/api/auth/logout", headers=admin_headers)
        await asyncio.sleep(2.1)
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
        assert not viewers.counts

    asyncio.run(consume())
    with pytest.raises(HTTPException) as expired:
        preview_access(request, camera_id)
    assert expired.value.status_code == 401
    assert client.get(f"/api/cameras/{camera_id}/preview").status_code == 401


def test_camera_grants_are_explicit_and_revocable(app_context, admin_headers):
    client, engine, _ = app_context
    camera_id = client.post("/api/cameras", json=PAYLOAD, headers=admin_headers).json()["camera_id"]
    other_id = client.post(
        "/api/cameras", json={**PAYLOAD, "name": "other camera"}, headers=admin_headers
    ).json()["camera_id"]
    with Session(engine) as db:
        db.add(
            User(
                username="operator",
                role="operator",
                password_hash=password_hasher.hash(TEST_PASSWORD),
            )
        )
        db.commit()
    assert (
        client.put(
            f"/api/cameras/{camera_id}/access",
            json={"username": "viewer", "can_operate": True},
            headers=admin_headers,
        ).status_code
        == 422
    )
    client.put(
        f"/api/cameras/{camera_id}/access",
        json={"username": "operator", "can_operate": True},
        headers=admin_headers,
    )
    login = client.post("/api/auth/login", json={"username": "operator", "password": TEST_PASSWORD})
    operator_headers = {"X-CSRF-Token": login.json()["csrf_token"]}
    replace_worker(client, lambda req: httpx.Response(200, json={"state": "opening"}))
    assert client.get(f"/api/cameras/{camera_id}/status").status_code == 200
    assert client.get(f"/api/cameras/{other_id}/status").status_code == 403
    assert client.get(f"/api/cameras/{other_id}/preview").status_code == 403
    assert (
        client.post(
            f"/api/cameras/{camera_id}/start", json={}, headers=operator_headers
        ).status_code
        == 200
    )
    assert (
        client.post(f"/api/cameras/{other_id}/start", json={}, headers=operator_headers).status_code
        == 403
    )
    request = SimpleNamespace(app=client.app, cookies=dict(client.cookies), method="GET")
    preview_access(request, camera_id)
    admin = client.post("/api/auth/login", json={"username": "admin", "password": TEST_PASSWORD})
    client.put(
        f"/api/cameras/{camera_id}/access",
        json={"username": "operator", "can_view": False},
        headers={"X-CSRF-Token": admin.json()["csrf_token"]},
    )
    with pytest.raises(HTTPException) as rejected:
        preview_access(request, camera_id)
    assert rejected.value.status_code == 403
