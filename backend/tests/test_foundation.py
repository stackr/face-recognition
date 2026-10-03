from datetime import timedelta

import pytest
from app.core.config import Settings
from app.core.security import COOKIE_NAME, token_hash
from app.models import AuditLog, AuthSession, Camera, User
from app.models.foundation import utc_now
from conftest import TEST_PASSWORD
from cryptography.fernet import Fernet
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

PAYLOAD = {
    "name": "시험 카메라",
    "location": "입구",
    "description": "phase1",
    "rtsp_url": "rtsp://private-user:private-password@127.0.0.1:8554/live?token=secret-token",
    "enabled": True,
}


def test_invalid_configuration_hides_secret_values():
    secret = "sensitive-short-secret"
    with pytest.raises(ValidationError) as captured:
        Settings(
            _env_file=None,
            session_secret=secret,
            service_token="t" * 48,
            rtsp_encryption_key=Fernet.generate_key().decode(),
        )
    assert secret not in str(captured.value)


def test_public_health_and_private_endpoints(client):
    assert client.get("/api/health").json() == {"status": "ok", "phase": 7}
    for path in ["/api/auth/me", "/api/cameras", "/api/system/status"]:
        assert client.get(path).status_code == 401


def test_session_storage_and_logout(app_context):
    client, engine, _ = app_context
    response = client.post("/api/auth/login", json={"username": "admin", "password": TEST_PASSWORD})
    assert response.status_code == 200
    assert TEST_PASSWORD not in response.text and "password_hash" not in response.text
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    token = client.cookies[COOKIE_NAME]
    with Session(engine) as db:
        stored = db.scalar(select(AuthSession))
        assert stored.token_hash == token_hash(token) and stored.token_hash != token
    assert client.get("/api/auth/me").json()["user"]["role"] == "admin"
    assert client.post("/api/auth/logout").status_code == 403
    assert (
        client.post(
            "/api/auth/logout", headers={"X-CSRF-Token": response.json()["csrf_token"]}
        ).status_code
        == 204
    )
    assert client.get("/api/auth/me").status_code == 401
    with Session(engine) as db:
        assert db.scalar(select(AuthSession)) is None


def test_invalid_login_is_generic_and_rate_limited(client):
    for username in ["admin", "unknown"]:
        response = client.post(
            "/api/auth/login", json={"username": username, "password": "incorrect"}
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid username or password"
    for _ in range(8):
        client.post("/api/auth/login", json={"username": "unknown", "password": "incorrect"})
    assert (
        client.post(
            "/api/auth/login", json={"username": "admin", "password": TEST_PASSWORD}
        ).status_code
        == 429
    )


def test_camera_crud_encrypts_credentials_and_audits(app_context, admin_headers):
    client, engine, settings = app_context
    response = client.post("/api/cameras", json=PAYLOAD, headers=admin_headers)
    assert response.status_code == 201
    camera = response.json()
    camera_id = camera["camera_id"]
    assert camera["rtsp_url"] == "rtsp://127.0.0.1:8554/live"
    assert "private-password" not in response.text and "secret-token" not in response.text
    with Session(engine) as db:
        stored = db.get(Camera, camera_id)
        assert "private-password" not in stored.rtsp_url_encrypted
        assert (
            Fernet(settings.rtsp_encryption_key.get_secret_value())
            .decrypt(stored.rtsp_url_encrypted.encode())
            .decode()
            == PAYLOAD["rtsp_url"]
        )
    assert len(client.get("/api/cameras").json()) == 1
    update = {**PAYLOAD, "name": "수정 카메라", "enabled": False}
    assert (
        client.put(f"/api/cameras/{camera_id}", json=update, headers=admin_headers).json()[
            "enabled"
        ]
        is False
    )
    assert client.get(f"/api/cameras/{camera_id}").json()["name"] == "수정 카메라"
    assert client.delete(f"/api/cameras/{camera_id}", headers=admin_headers).status_code == 204
    assert client.get(f"/api/cameras/{camera_id}").status_code == 404
    with Session(engine) as db:
        actions = db.scalars(
            select(AuditLog.action).where(AuditLog.resource_type == "camera")
        ).all()
        assert actions == ["camera.create", "camera.update", "camera.delete"]


def test_duplicate_name_conflict_does_not_create_an_extra_camera(client, admin_headers):
    assert client.post("/api/cameras", json=PAYLOAD, headers=admin_headers).status_code == 201
    response = client.post("/api/cameras", json=PAYLOAD, headers=admin_headers)
    assert response.status_code == 409 and "private-password" not in response.text
    assert len(client.get("/api/cameras").json()) == 1


@pytest.mark.parametrize("stale_rtsp", ["not-an-rtsp-url", PAYLOAD["rtsp_url"]])
def test_mp4_create_and_update_ignore_hidden_rtsp_values(app_context, admin_headers, stale_rtsp):
    client, engine, settings = app_context
    payload = {**PAYLOAD, "source_type": "mp4", "rtsp_url": stale_rtsp}
    response = client.post("/api/cameras", json=payload, headers=admin_headers)
    assert response.status_code == 201
    camera_id = response.json()["camera_id"]
    assert response.json()["rtsp_url"] == "" and not response.json()["has_test_video"]
    response = client.put(f"/api/cameras/{camera_id}", json=payload, headers=admin_headers)
    assert response.status_code == 200 and response.json()["rtsp_url"] == ""
    with Session(engine) as db:
        stored = db.get(Camera, camera_id)
        assert stored.source_type == "mp4"
        assert (
            Fernet(settings.rtsp_encryption_key.get_secret_value()).decrypt(
                stored.rtsp_url_encrypted.encode()
            )
            == b""
        )


def test_rtsp_sources_still_require_a_valid_url(client, admin_headers):
    for rtsp_url in ("", "not-an-rtsp-url"):
        response = client.post(
            "/api/cameras", json={**PAYLOAD, "rtsp_url": rtsp_url}, headers=admin_headers
        )
        assert response.status_code == 422
    assert client.get("/api/cameras").json() == []


def test_csrf_origin_and_role_permissions(client, admin_headers):
    assert client.post("/api/cameras", json=PAYLOAD).status_code == 403
    assert (
        client.post(
            "/api/cameras",
            json=PAYLOAD,
            headers={**admin_headers, "Origin": "https://untrusted.example"},
        ).status_code
        == 403
    )
    login = client.post("/api/auth/login", json={"username": "viewer", "password": TEST_PASSWORD})
    headers = {"X-CSRF-Token": login.json()["csrf_token"]}
    assert client.get("/api/cameras").status_code == 200
    assert client.post("/api/cameras", json=PAYLOAD, headers=headers).status_code == 403


def test_expired_or_disabled_accounts_are_rejected(app_context, admin_headers):
    client, engine, _ = app_context
    with Session(engine) as db:
        session = db.scalar(select(AuthSession))
        session.expires_at = utc_now() - timedelta(seconds=1)
        db.commit()
    assert client.get("/api/auth/me").status_code == 401
    client.post("/api/auth/login", json={"username": "admin", "password": TEST_PASSWORD})
    with Session(engine) as db:
        db.scalar(select(User).where(User.username == "admin")).enabled = False
        db.commit()
    assert client.get("/api/auth/me").status_code == 401


def test_validation_does_not_echo_secrets(client, admin_headers):
    payload = {**PAYLOAD, "rtsp_url": "invalid://private-user:private-password@host"}
    response = client.post("/api/cameras", json=payload, headers=admin_headers)
    assert response.status_code == 422
    assert "private-password" not in response.text and "input" not in response.text
    assert client.get("/api/cameras?limit=9999").status_code == 422


def test_dependency_health_and_saved_gpu_report(app_context, admin_headers):
    import json

    client, _, settings = app_context
    response = client.get("/api/system/status")
    assert response.json()["services"] == {"mariadb": {"status": "ok"}, "qdrant": {"status": "ok"}}
    assert response.json()["gpu"]["status"] == "unverified"
    settings.gpu_report_path.write_text(
        json.dumps(
            {
                "status": "passed",
                "actual_device": "cuda",
                "gpu_name": "Test GPU",
                "cuda_node_count": 3,
                "checked_at": "2026-10-02T00:00:00Z",
            }
        )
    )
    assert client.get("/api/system/status").json()["gpu"]["cuda_node_count"] == 3
    settings.gpu_report_path.write_text("invalid JSON")
    assert client.get("/api/system/status").json()["gpu"]["status"] == "invalid_report"
