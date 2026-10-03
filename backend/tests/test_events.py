"""Phase 6 uses a private SQLite fixture; no native camera or GPU is opened."""

import asyncio
import importlib.util
import json
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.api import events as routes
from app.core.config import Settings
from app.core.face_data import MODEL_VERSION
from app.core.security import COOKIE_NAME, csrf_token, token_hash
from app.db.session import get_db
from app.models import (
    AuditLog,
    AuthSession,
    Base,
    Camera,
    CameraPermission,
    EventChange,
    EventState,
    FunctionSettings,
    GalleryState,
    MatchEvent,
    Person,
    PersonFace,
    PersonPermission,
    Track,
    User,
)
from app.models.foundation import utc_now
from app.services.event_stream import EventBroker
from app.services.events import Candidate, EventStore
from app.worker.events import EventWriter
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session


@pytest.fixture
def event_context(tmp_path):
    settings = Settings(
        _env_file=None,
        session_secret="s" * 48,
        service_token="t" * 48,
        rtsp_encryption_key=Fernet.generate_key().decode(),
        event_dir=tmp_path / "events",
        event_ws_queue_size=2,
        event_queue_size=2,
    )
    engine = create_engine(f"sqlite:///{tmp_path / 'events.sqlite'}", hide_parameters=True)

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([EventState(id=1, last_event_id=0, revision=0), GalleryState(id=1, revision=1)])
        db.add_all(
            [
                User(id=1, username="admin", role="admin", password_hash="unused"),
                User(id=2, username="operator", role="operator", password_hash="unused"),
                User(id=3, username="viewer", role="viewer", password_hash="unused"),
            ]
        )
        db.add(Camera(id=1, name="unit-camera", rtsp_url_encrypted="unused"))
        db.add(Person(id=1, name="unit-person"))
        db.commit()
        db.add(
            PersonFace(
                id=1,
                person_id=1,
                embedding_id=str(uuid.uuid4()),
                embedding_encrypted="unit-test-only",
                model_version=MODEL_VERSION,
                quality=0.9,
                state="ready",
                image_expires_at=utc_now() + timedelta(days=1),
                embedding_expires_at=utc_now() + timedelta(days=1),
            )
        )
        for user_id in (1, 2, 3):
            db.add(
                AuthSession(
                    token_hash=token_hash(f"token-{user_id}"),
                    user_id=user_id,
                    expires_at=utc_now() + timedelta(hours=1),
                )
            )
        db.commit()
    store = EventStore(settings, engine)
    candidate = Candidate(
        1,
        "a" * 32,
        1,
        1,
        1,
        1,
        datetime.now(UTC).isoformat(),
        0.85,
        0.85,
        b"\xff\xd8face",
        b"\xff\xd8frame",
    )
    yield SimpleNamespace(settings=settings, engine=engine, store=store, candidate=candidate)
    engine.dispose()


def grant(context, *, operate=False, person=True, camera=True, user_id=2):
    with Session(context.engine) as db:
        if camera:
            db.merge(CameraPermission(camera_id=1, user_id=user_id, can_operate=operate))
        if person:
            db.merge(PersonPermission(person_id=1, user_id=user_id))
        db.commit()


def test_concurrent_retries_deduplicate_and_sessions_have_independent_tracks(event_context):
    c = event_context
    with ThreadPoolExecutor(max_workers=8) as pool:
        result = list(pool.map(c.store.record, [c.candidate] * 16))
    assert result.count(1) == 1 and result.count(None) == 15
    assert c.store.record(replace(c.candidate, stream_session_id="b" * 32)) == 2
    assert c.store.record(replace(c.candidate, track_id=2)) == 3
    with Session(c.engine) as db:
        assert db.scalar(select(func.count()).select_from(MatchEvent)) == 3
        assert db.scalar(select(func.count()).select_from(Track)) == 3
        assert db.get(EventState, 1).revision == 3
    assert len(list(c.settings.event_dir.glob("*.jpg"))) == 6
    assert os.stat(c.settings.event_dir).st_mode & 0o777 == 0o700
    assert all(
        os.stat(path).st_mode & 0o777 == 0o600 for path in c.settings.event_dir.glob("*.jpg")
    )


def test_cooldown_improvement_and_review_keep_one_coherent_event(event_context):
    c = event_context
    assert c.store.record(c.candidate) == 1
    improved = replace(c.candidate, quality=0.95, similarity=0.9, face_jpeg=b"\xff\xd8better")
    assert c.store.record(improved) is None
    with Session(c.engine) as db:
        row = db.get(MatchEvent, 1)
        original = row.face_image_path
        row.updated_at = utc_now() - timedelta(seconds=31)
        db.commit()
    assert c.store.record(improved) == 1
    assert not c.store.path(original).exists()
    with Session(c.engine) as db:
        assert db.get(EventState, 1).revision == 2
        assert db.get(MatchEvent, 1).face_quality == 0.95
        admin = db.get(User, 1)
        reviewed = c.store.review(1, admin, "confirmed")
        assert reviewed["change_id"] == 3
        assert c.store.review(1, admin, "confirmed")["change_id"] == 3
    assert c.store.record(replace(improved, quality=0.99)) is None
    with Session(c.engine) as db:
        assert db.get(MatchEvent, 1).status == "confirmed"
        assert db.scalar(select(func.count()).select_from(AuditLog)) == 1


def test_event_deletion_removes_images_recovers_offline_and_prevents_recreation(event_context):
    c = event_context
    c.store.record(c.candidate)
    with Session(c.engine) as db:
        admin = db.get(User, 1)
        original = db.get(MatchEvent, 1)
        paths = [original.face_image_path, original.frame_image_path]
        deleted = c.store.delete([1], admin)
        assert deleted == [{"type": "event_deleted", "event_id": 1, "change_id": 2}]
        assert c.store.delete([1, 1], admin) == deleted
    assert all(not c.store.path(name).exists() for name in paths)
    # Better evidence from the same ongoing track must not resurrect the deleted ID.
    assert c.store.record(replace(c.candidate, quality=0.99, similarity=0.99)) is None
    with Session(c.engine) as db:
        admin = db.get(User, 1)
        assert not c.store.page(db, admin)["items"]
        assert not c.store.page(db, admin, latest=True)["items"]
        assert c.store.page(db, admin, after_change_id=1)["items"] == deleted
        assert c.store.page(db, admin, after_change_id=0, limit=1)["has_more"]
        for action in (
            lambda: c.store.find(db, admin, 1),
            lambda: c.store.review(1, admin, "confirmed"),
        ):
            with pytest.raises(HTTPException) as unavailable:
                action()
            assert unavailable.value.status_code == 404
        assert db.get(EventState, 1).revision == 2
        assert db.scalar(select(func.count()).select_from(AuditLog)) == 1
    # A new stream session is a distinct detection, and remains eligible.
    assert c.store.record(replace(c.candidate, stream_session_id="b" * 32)) == 2
    with Session(c.engine) as db:
        assert [row["event_id"] for row in c.store.page(db, db.get(User, 1))["items"]] == [2]
        db.get(MatchEvent, 1).expires_at = utc_now() - timedelta(seconds=1)
        db.commit()
    c.store.cleanup()
    with Session(c.engine) as db:
        assert db.get(MatchEvent, 1) is None
        assert db.get(EventChange, 2) is None


def test_batch_deletion_is_atomic_and_tombstones_recheck_current_grants(event_context):
    c = event_context
    c.store.record(c.candidate)
    with Session(c.engine) as db:
        db.add(Camera(id=2, name="other-camera", rtsp_url_encrypted="unused"))
        db.commit()
    c.store.record(replace(c.candidate, camera_id=2))
    grant(c, operate=True)
    with Session(c.engine) as db:
        operator = db.get(User, 2)
        with pytest.raises(HTTPException) as denied:
            c.store.delete([1, 2], operator)
        assert denied.value.status_code == 404
        assert db.get(MatchEvent, 1).status == "candidate"
        assert len(list(c.settings.event_dir.glob("*.jpg"))) == 4
        assert db.get(EventState, 1).revision == 2
        db.get(CameraPermission, (1, 2)).can_operate = False
        db.commit()
        with pytest.raises(HTTPException) as denied:
            c.store.delete([1], operator)
        assert denied.value.status_code == 403
        assert not c.store.page(db, operator)["items"][0]["can_delete"]
    grant(c, operate=True)
    grant(c, operate=True, user_id=3)
    with Session(c.engine) as db:
        with pytest.raises(HTTPException) as denied:
            c.store.delete([1], db.get(User, 3))
        assert denied.value.status_code == 403
        c.store.delete([1], db.get(User, 2))
    app = SimpleNamespace(state=SimpleNamespace(engine=c.engine, events=c.store))
    assert routes.socket_data(app, "token-2", 1)[1]["type"] == "event_deleted"
    with Session(c.engine) as db:
        assert (
            c.store.page(db, db.get(User, 2), after_change_id=2)["items"][0]["type"]
            == "event_deleted"
        )
        db.delete(db.get(PersonPermission, (1, 2)))
        db.commit()
    assert routes.socket_data(app, "token-2", 1)[1] is None
    with Session(c.engine) as db:
        assert not c.store.page(db, db.get(User, 2), after_change_id=2)["items"]


@pytest.mark.parametrize("invalid", ["revision", "person", "face", "camera", "threshold"])
def test_stale_deleted_disabled_expired_and_low_score_candidates_fail_closed(
    event_context, invalid
):
    c = event_context
    candidate = c.candidate
    with Session(c.engine) as db:
        if invalid == "revision":
            db.get(GalleryState, 1).revision += 1
        elif invalid == "person":
            db.get(Person, 1).deleting = True
        elif invalid == "face":
            db.get(PersonFace, 1).embedding_expires_at = utc_now() - timedelta(seconds=1)
        elif invalid == "camera":
            db.get(Camera, 1).enabled = False
        else:
            candidate = replace(candidate, similarity=0.5)
        db.commit()
    assert c.store.record(candidate) is None
    assert not list(c.settings.event_dir.glob("*.jpg"))


def test_event_save_checks_saved_threshold_even_with_stale_worker_settings(event_context):
    c = event_context
    with Session(c.engine) as db:
        db.add(FunctionSettings(id=1, revision=1, values={"face_match_threshold": 0.6}))
        db.commit()
    # Lowered criteria must accept eligible scores even when this process still
    # holds the old environment threshold. Stricter criteria reject queued work.
    assert c.store.record(replace(c.candidate, similarity=0.7)) == 1
    with Session(c.engine) as db:
        row = db.get(FunctionSettings, 1)
        row.values, row.revision = {"face_match_threshold": 0.95}, 2
        db.commit()
    assert c.store.record(replace(c.candidate, track_id=2, similarity=0.9)) is None
    assert c.store.record(replace(c.candidate, track_id=2, similarity=0.96)) == 2
    assert len(list(c.settings.event_dir.glob("*.jpg"))) == 4


def test_file_failure_rolls_back_ids_tracks_and_partial_files(event_context, monkeypatch):
    c = event_context
    original = c.store.write_image
    calls = 0

    def fail_second(content):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated disk failure")
        return original(content)

    monkeypatch.setattr(c.store, "write_image", fail_second)
    with pytest.raises(OSError):
        c.store.record(c.candidate)
    assert not list(c.settings.event_dir.glob("*.jpg"))
    with Session(c.engine) as db:
        assert db.get(EventState, 1).last_event_id == 0
        assert db.scalar(select(func.count()).select_from(Track)) == 0
    monkeypatch.setattr(c.store, "write_image", original)
    assert c.store.record(c.candidate) == 1


def test_pagination_recovers_creates_and_reviews_with_both_grants(event_context):
    c = event_context
    for track_id in range(1, 4):
        c.store.record(replace(c.candidate, track_id=track_id))
    with Session(c.engine) as db:
        admin, operator = db.get(User, 1), db.get(User, 2)
        c.store.review(1, admin, "rejected")
        first = c.store.page(db, admin, limit=2)
        assert [item["event_id"] for item in first["items"]] == [1, 2]
        assert first["has_more"] and first["next_cursor"] == 2
        next_page = c.store.page(db, admin, after_id=2)
        assert [item["event_id"] for item in next_page["items"]] == [3]
        updates = c.store.page(db, admin, after_change_id=3)
        assert updates["items"][0]["status"] == "rejected" and updates["next_cursor"] == 4
        assert not c.store.page(db, operator)["items"]
    grant(c, person=False)
    with Session(c.engine) as db:
        assert not c.store.page(db, db.get(User, 2))["items"]
    grant(c, operate=False)
    with Session(c.engine) as db:
        operator = db.get(User, 2)
        assert len(c.store.page(db, operator)["items"]) == 3
        with pytest.raises(HTTPException) as denied:
            c.store.review(1, operator, "confirmed")
        assert denied.value.status_code == 403
    grant(c, operate=True)
    with Session(c.engine) as db:
        assert c.store.review(1, db.get(User, 2), "confirmed")["status"] == "confirmed"


def test_retention_deletion_and_orphan_files_are_cleaned(event_context):
    c = event_context
    c.store.record(c.candidate)
    orphan = c.store.write_image(b"\xff\xd8orphan")
    os.utime(c.store.path(orphan), (0, 0))
    with Session(c.engine) as db:
        row = db.get(MatchEvent, 1)
        row.image_expires_at = utc_now() - timedelta(seconds=1)
        db.commit()
    c.store.cleanup()
    assert not list(c.settings.event_dir.glob("*.jpg"))
    with Session(c.engine) as db:
        row = db.get(MatchEvent, 1)
        assert row and row.face_image_path is None
        row.expires_at = utc_now() - timedelta(seconds=1)
        db.commit()
    c.store.cleanup()
    with Session(c.engine) as db:
        assert db.get(MatchEvent, 1) is None and db.get(EventChange, 1) is None
    c.store.record(replace(c.candidate, stream_session_id="b" * 32))
    with Session(c.engine) as db:
        db.delete(db.get(Person, 1))
        db.commit()
    c.store.cleanup()
    assert not list(c.settings.event_dir.glob("*.jpg"))
    with Session(c.engine) as db:
        assert db.scalar(select(func.count()).select_from(MatchEvent)) == 0


def test_storage_limit_does_not_leave_half_events(event_context):
    c = event_context
    c.settings.event_storage_max_mb = 0
    with pytest.raises(OverflowError):
        c.store.record(c.candidate)
    assert not list(c.settings.event_dir.glob("*.jpg"))
    with Session(c.engine) as db:
        assert db.get(EventState, 1).last_event_id == 0


def test_ambiguous_commit_preserves_files_for_durable_event(event_context, monkeypatch):
    c = event_context
    commit = Session.commit

    def committed_but_lost_reply(db):
        commit(db)
        raise RuntimeError("simulated lost commit acknowledgement")

    monkeypatch.setattr(Session, "commit", committed_but_lost_reply)
    with pytest.raises(RuntimeError):
        c.store.record(c.candidate)
    monkeypatch.setattr(Session, "commit", commit)
    with Session(c.engine) as db:
        row = db.get(MatchEvent, 1)
        assert c.store.path(row.face_image_path).exists()
        assert c.store.path(row.frame_image_path).exists()
    assert c.store.record(c.candidate) is None


def test_pending_person_deletion_removes_events_before_gallery_ack(event_context):
    c = event_context
    c.store.record(c.candidate)
    with Session(c.engine) as db:
        db.get(Person, 1).deleting = True
        db.commit()
        assert not c.store.page(db, db.get(User, 1))["items"]
    c.store.cleanup()
    with Session(c.engine) as db:
        assert db.get(Person, 1) and db.get(MatchEvent, 1) is None
    assert not list(c.settings.event_dir.glob("*.jpg"))


def test_worker_notification_failure_keeps_durable_event_and_retries_are_bounded(event_context):
    c = event_context
    attempts = []
    writer = EventWriter(
        c.settings,
        c.engine,
        httpx.MockTransport(lambda request: attempts.append(request) or httpx.Response(503)),
    )
    try:
        assert writer.submit(c.candidate)
        writer.queue.join()
        assert len(attempts) == 3
        assert writer.status()["notification_failures"] == 3
        with Session(c.engine) as db:
            assert db.get(MatchEvent, 1) and db.get(EventChange, 1)
    finally:
        writer.close()


def test_worker_submission_cooldown_does_not_cross_session(event_context):
    c = event_context
    writer = EventWriter.__new__(EventWriter)
    writer.settings = c.settings
    submitted = []
    writer.submit = lambda candidate: submitted.append(candidate) or True
    best = {
        "stream_session_id": "a" * 32,
        "captured_at": c.candidate.timestamp,
        "quality": 0.9,
        "jpeg": b"\xff\xd8face",
        "frame_jpeg": b"\xff\xd8frame",
    }
    faces = SimpleNamespace(lock=threading.RLock(), tracks={1: {"best": best}})
    tracks = [
        {"track_id": 1, "face": {"matches": [{"person_id": 1, "face_id": 1, "similarity": 0.9}]}}
    ]
    writer.submit_tracks(1, "a" * 32, faces, tracks, 1, 10)
    writer.submit_tracks(1, "a" * 32, faces, tracks, 1, 11)
    writer.submit_tracks(1, "b" * 32, faces, tracks, 1, 11)
    assert len(submitted) == 1
    faces.tracks = {1: {"best": best | {"stream_session_id": "b" * 32}}}
    writer.submit_tracks(1, "b" * 32, faces, tracks, 1, 11)
    assert len(submitted) == 2


def test_migration_matches_models_and_seeds_ordered_cursor(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.sqlite'}")
    added = {"event_state", "tracks", "match_events", "event_changes"}
    Base.metadata.create_all(
        engine, tables=[table for table in Base.metadata.sorted_tables if table.name not in added]
    )
    migration_path = Path(__file__).parents[1] / "migrations/versions/0005_match_events.py"
    spec = importlib.util.spec_from_file_location("event_migration", migration_path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()
        from alembic.autogenerate import compare_metadata

        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    engine.dispose()


def test_http_event_routes_auth_csrf_images_grants_and_review(event_context, monkeypatch):
    c = event_context
    c.store.record(c.candidate)
    app = FastAPI()
    app.state.settings, app.state.engine = c.settings, c.engine
    app.state.events, app.state.event_broker = c.store, EventBroker(c.store)
    app.include_router(routes.router)

    async def database():
        with Session(c.engine) as db:
            yield db

    app.dependency_overrides[get_db] = database

    # Test-only synchronous dispatch: sandbox forbids cross-thread asyncio wakeups.
    async def inline(function, /, *args, **kwargs):
        return function(*args, **kwargs)

    import fastapi.dependencies.utils
    import fastapi.routing

    monkeypatch.setattr(fastapi.dependencies.utils, "run_in_threadpool", inline)
    monkeypatch.setattr(fastapi.routing, "run_in_threadpool", inline)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/api/events")).status_code == 401
            client.cookies.set(COOKIE_NAME, "token-1")
            assert (await client.get("/api/events?limit=1")).json()["items"][0]["event_id"] == 1
            assert (await client.get("/api/events?limit=101")).status_code == 422
            assert (await client.get("/api/events?after_id=1&after_change_id=1")).status_code == 422
            assert (await client.get("/api/events?latest=true")).json()["change_cursor"] == 1
            assert (await client.get("/api/events/1/face")).content == c.candidate.face_jpeg
            assert (await client.post("/api/events/1/confirm")).status_code == 403
            assert (await client.delete("/api/events/1")).status_code == 403
            assert (
                await client.post("/api/events/delete", json={"event_ids": [1]})
            ).status_code == 403
            headers = {
                "X-CSRF-Token": csrf_token("token-1", c.settings.session_secret.get_secret_value())
            }
            assert (await client.post("/api/events/1/confirm", headers=headers)).json()[
                "status"
            ] == "confirmed"
            client.cookies.set(COOKIE_NAME, "token-3")
            assert not (await client.get("/api/events")).json()["items"]
            assert (await client.get("/api/events/1/face")).status_code == 404
            grant(c, user_id=3)
            assert (await client.get("/api/events/1/frame")).status_code == 200
            viewer_headers = {
                "X-CSRF-Token": csrf_token("token-3", c.settings.session_secret.get_secret_value())
            }
            assert (
                await client.post("/api/events/1/reject", headers=viewer_headers)
            ).status_code == 403
            assert (await client.delete("/api/events/1", headers=viewer_headers)).status_code == 403
            assert (
                await client.post(
                    "/api/events/delete", json={"event_ids": [1]}, headers=viewer_headers
                )
            ).status_code == 403
            with Session(c.engine) as db:
                db.delete(db.get(PersonPermission, (1, 3)))
                db.commit()
            assert (await client.get("/api/events/1/frame")).status_code == 404
            assert (
                await client.post("/internal/events/notify", json={"event_id": 1})
            ).status_code == 401
            assert (
                await client.post(
                    "/internal/events/notify",
                    json={"event_id": 1},
                    headers={"X-Service-Token": c.settings.service_token.get_secret_value()},
                )
            ).status_code == 200
            client.cookies.set(COOKIE_NAME, "token-1")
            for ids in ([], [1] * 101, [True], [0], ["1"]):
                assert (
                    await client.post(
                        "/api/events/delete", json={"event_ids": ids}, headers=headers
                    )
                ).status_code == 422
            c.store.record(replace(c.candidate, track_id=2))
            deletion = await client.delete("/api/events/1", headers=headers)
            assert deletion.json() == {"type": "event_deleted", "event_id": 1, "change_id": 4}
            for path in ("/api/events/1", "/api/events/1/face", "/api/events/1/frame"):
                assert (await client.get(path)).status_code == 404
            deleted = await client.post(
                "/api/events/delete", json={"event_ids": [1, 2]}, headers=headers
            )
            assert deleted.status_code == 200
            assert [row["event_id"] for row in deleted.json()["items"]] == [1, 2]
            assert not (await client.get("/api/events?latest=true")).json()["items"]

    asyncio.run(run())


@pytest.mark.parametrize(
    "origin,token,accepted",
    [
        ("http://localhost:4200", "token-1", True),
        ("http://untrusted.test", "token-1", False),
        (None, "token-1", False),
        ("http://localhost:4200", "missing", False),
    ],
)
def test_websocket_handshake_delivery_and_origin_auth(
    event_context, monkeypatch, origin, token, accepted
):
    c = event_context
    c.store.record(c.candidate)

    async def inline(function, /, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", inline)

    async def run():
        app = FastAPI()
        app.state.settings, app.state.engine = c.settings, c.engine
        app.state.events, app.state.event_broker = c.store, EventBroker(c.store)
        app.include_router(routes.router)
        incoming, outgoing = asyncio.Queue(), asyncio.Queue()
        headers = [(b"cookie", f"{COOKIE_NAME}={token}".encode())]
        if origin:
            headers.append((b"origin", origin.encode()))
        scope = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "path": "/ws/events",
            "raw_path": b"/ws/events",
            "query_string": b"",
            "headers": headers,
            "scheme": "ws",
            "client": ("127.0.0.1", 1),
            "server": ("test", 80),
            "root_path": "",
        }
        await incoming.put({"type": "websocket.connect"})
        task = asyncio.create_task(app(scope, incoming.get, outgoing.put))
        first = await asyncio.wait_for(outgoing.get(), 1)
        if accepted:
            assert first["type"] == "websocket.accept"
            assert json.loads((await outgoing.get())["text"])["type"] == "ready"
            app.state.event_broker.publish(1)
            delivered = json.loads((await asyncio.wait_for(outgoing.get(), 1))["text"])
            assert delivered["event_id"] == 1 and delivered["status"] == "candidate"
            with Session(c.engine) as db:
                c.store.delete([1], db.get(User, 1))
            app.state.event_broker.publish(1)
            deleted = json.loads((await asyncio.wait_for(outgoing.get(), 1))["text"])
            assert deleted == {"type": "event_deleted", "event_id": 1, "change_id": 2}
            await incoming.put({"type": "websocket.disconnect", "code": 1000})
        else:
            assert first["type"] == "websocket.close" and first["code"] == 1008
        await asyncio.wait_for(task, 1)
        assert not app.state.event_broker.clients

    asyncio.run(run())


def test_socket_revocation_and_bounded_slow_client_queue(event_context):
    c = event_context
    c.store.record(c.candidate)
    grant(c)
    app = SimpleNamespace(state=SimpleNamespace(engine=c.engine, events=c.store))
    signature, payload = routes.socket_data(app, "token-2", 1)
    assert payload["event_id"] == 1
    with Session(c.engine) as db:
        db.delete(db.get(PersonPermission, (1, 2)))
        db.commit()
    new_signature, payload = routes.socket_data(app, "token-2", 1)
    assert new_signature != signature and payload is None
    with Session(c.engine) as db:
        db.get(User, 2).enabled = False
        db.commit()
    with pytest.raises(HTTPException):
        routes.socket_data(app, "token-2", 1)

    async def run():
        broker = EventBroker(c.store)
        subscriber = broker.subscribe()
        for _ in range(10):
            broker.publish(1)
        assert subscriber.queue.qsize() == 2 and subscriber.overflow.is_set()
        assert broker.read_changes()[1] == []
        broker.cursor = 0
        assert broker.read_changes() == (1, [1])

    asyncio.run(run())


def test_event_frame_is_from_the_best_face_frame_and_shared_per_analysis_tick(event_context):
    import io

    import numpy as np
    from app.worker.faces import FaceCandidate, TrackFaces
    from app.worker.runtime import Frame
    from PIL import Image

    c = event_context

    class Analyzer:
        def inspect(self, image, bbox):
            return FaceCandidate(
                {"status": "accepted", "quality": 0.9}, np.zeros((112, 112, 3), dtype=np.uint8)
            )

        def embed(self, image):
            return np.ones(512, dtype=np.float32)

    faces = TrackFaces(c.settings)
    frame = Frame(np.zeros((1080, 1920, 3), dtype=np.uint8), "a" * 32, 1, c.candidate.timestamp, 10)
    tracks = [{"track_id": track_id, "bbox": [0, 0, 1920, 1080]} for track_id in (1, 2)]
    faces.process(Analyzer(), frame, tracks, {1, 2})
    first, second = (faces.tracks[key]["best"] for key in (1, 2))
    assert first["frame_jpeg"] is second["frame_jpeg"]
    assert Image.open(io.BytesIO(first["frame_jpeg"])).size == (1280, 720)
    assert Image.open(io.BytesIO(first["jpeg"])).size == (112, 112)
    assert first["captured_at"] == frame.captured_at and first["frame_id"] == 1


def test_broker_initializes_before_ready_and_recovers_a_lost_wakeup(event_context, monkeypatch):
    c = event_context
    c.store.record(c.candidate)

    async def inline(function, /, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", inline)

    async def run():
        broker = EventBroker(c.store)
        await broker.start()
        assert broker.cursor == 1
        subscriber = broker.subscribe()
        try:
            c.store.record(replace(c.candidate, track_id=2))
            # No internal notify: the periodic journal tail still delivers it.
            assert await asyncio.wait_for(subscriber.queue.get(), 2) == 2
        finally:
            await broker.close()

    asyncio.run(run())


def test_broker_startup_requires_event_schema(event_context, monkeypatch):
    c = event_context

    async def inline(function, /, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", inline)
    with c.engine.begin() as connection:
        EventState.__table__.drop(connection)

    async def run():
        broker = EventBroker(c.store)
        with pytest.raises(RuntimeError, match="Event storage unavailable"):
            await broker.start()
        assert broker.task is None

    asyncio.run(run())


@pytest.mark.parametrize("slow", ["queue", "send"])
def test_slow_websocket_is_closed_and_releases_slot(event_context, monkeypatch, slow):
    c = event_context
    c.store.record(c.candidate)
    c.settings.event_ws_send_timeout_seconds = 0.1

    async def inline(function, /, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", inline)

    async def run():
        app = FastAPI()
        app.state.settings, app.state.engine = c.settings, c.engine
        app.state.events, app.state.event_broker = c.store, EventBroker(c.store)
        app.include_router(routes.router)
        incoming, outgoing = asyncio.Queue(), asyncio.Queue()

        async def send(message):
            if (
                slow == "send"
                and "text" in message
                and json.loads(message["text"]).get("type") == "person_match"
            ):
                await asyncio.sleep(2)
            await outgoing.put(message)

        scope = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "path": "/ws/events",
            "raw_path": b"/ws/events",
            "query_string": b"",
            "headers": [
                (b"origin", b"http://localhost:4200"),
                (b"cookie", f"{COOKIE_NAME}=token-1".encode()),
            ],
            "scheme": "ws",
            "client": ("127.0.0.1", 1),
            "server": ("test", 80),
            "root_path": "",
        }
        await incoming.put({"type": "websocket.connect"})
        task = asyncio.create_task(app(scope, incoming.get, send))
        assert (await outgoing.get())["type"] == "websocket.accept"
        assert json.loads((await outgoing.get())["text"])["type"] == "ready"
        for _ in range(10 if slow == "queue" else 1):
            app.state.event_broker.publish(1)
        closed = await asyncio.wait_for(outgoing.get(), 1)
        assert closed["type"] == "websocket.close" and closed["code"] == 1013
        await asyncio.wait_for(task, 1)
        assert not app.state.event_broker.clients

    asyncio.run(run())
