import httpx
import pytest
from app.core.config import Settings
from app.core.security import password_hasher
from app.main import create_app
from app.models import Base, EventState, GalleryState, User
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

TEST_PASSWORD = "test-password-long-enough"


@pytest.fixture
def app_context(tmp_path):
    settings = Settings(
        _env_file=None,
        session_secret="s" * 48,
        service_token="t" * 48,
        rtsp_encryption_key=Fernet.generate_key().decode(),
        log_dir=tmp_path / "logs",
        gpu_report_path=tmp_path / "gpu.json",
        video_dir=tmp_path / "videos",
        face_test_dir=tmp_path / "face-tests",
        reference_dir=tmp_path / "references",
        event_dir=tmp_path / "events",
        clip_dir=tmp_path / "clips",
        clip_buffer_dir=tmp_path / "clip-buffer",
        allowed_origins=["http://localhost:4200"],
    )
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        hide_parameters=True,
    )

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(GalleryState(id=1, revision=1))
        db.add(EventState(id=1, revision=0, last_event_id=0))
        hashed = password_hasher.hash(TEST_PASSWORD)
        db.add_all(
            [
                User(username="admin", role="admin", password_hash=hashed),
                User(username="viewer", role="viewer", password_hash=hashed),
            ]
        )
        db.commit()
    vector = QdrantClient(":memory:")
    transport = httpx.MockTransport(lambda request: httpx.Response(404))
    app = create_app(
        settings=settings,
        engine=engine,
        vector_client=vector,
        worker_transport=transport,
        start_cleanup=False,
    )
    with TestClient(app) as client:
        yield client, engine, settings


@pytest.fixture
def client(app_context):
    return app_context[0]


@pytest.fixture
def admin_headers(client):
    response = client.post("/api/auth/login", json={"username": "admin", "password": TEST_PASSWORD})
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrf_token"]}
