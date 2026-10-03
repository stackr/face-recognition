import os
import uuid

import pytest
from app.core.config import Settings
from app.core.security import password_hasher
from app.db.session import make_engine
from app.main import create_app
from app.models import AuditLog, AuthSession, Camera, User
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient
from sqlalchemy import delete, inspect, text
from sqlalchemy.orm import Session


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("CCTV_RUN_INTEGRATION") != "1", reason="Opt in to real service integration"
)
def test_migrated_mariadb_auth_and_crud():
    settings = Settings()
    engine = make_engine(settings)
    tables = set(inspect(engine).get_table_names())
    assert {"users", "auth_sessions", "cameras", "audit_logs", "alembic_version"} <= tables
    assert {"tracks", "match_events", "event_state", "event_changes"} <= tables
    assert {"function_settings", "recognition_logs"} <= tables
    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0006_recognition_controls"
        )
    name = "integration_" + uuid.uuid4().hex[:16]
    password = uuid.uuid4().hex
    with Session(engine) as db:
        user = User(username=name, password_hash=password_hasher.hash(password), role="admin")
        db.add(user)
        db.commit()
        user_id = user.id
    camera_id = None
    vector = QdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key.get_secret_value() or None,
        timeout=3,
    )
    try:
        app = create_app(settings, engine, vector)
        with TestClient(app) as client:
            login = client.post("/api/auth/login", json={"username": name, "password": password})
            assert login.status_code == 200
            headers = {"X-CSRF-Token": login.json()["csrf_token"]}
            payload = {
                "name": name,
                "rtsp_url": "rtsp://test:temporary@127.0.0.1/test",
                "location": "test",
            }
            camera = client.post("/api/cameras", json=payload, headers=headers)
            assert camera.status_code == 201
            camera_id = camera.json()["camera_id"]
            assert client.get(f"/api/cameras/{camera_id}").status_code == 200
            assert (
                client.put(
                    f"/api/cameras/{camera_id}", json={**payload, "enabled": False}, headers=headers
                ).status_code
                == 200
            )
            status = client.get("/api/system/status").json()
            assert status["services"]["qdrant"]["status"] == "ok"
            assert client.delete(f"/api/cameras/{camera_id}", headers=headers).status_code == 204
            camera_id = None
            assert client.post("/api/auth/logout", headers=headers).status_code == 204
    finally:
        # Remove only records created by this test, never reset the schema.
        with Session(engine) as db:
            if camera_id is not None:
                db.execute(delete(Camera).where(Camera.id == camera_id, Camera.name == name))
            db.execute(delete(AuditLog).where(AuditLog.user_id == user_id))
            db.execute(delete(AuthSession).where(AuthSession.user_id == user_id))
            db.execute(delete(User).where(User.id == user_id, User.username == name))
            db.commit()
        engine.dispose()
