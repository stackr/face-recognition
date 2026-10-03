import asyncio
import json
import subprocess
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
from app.api import events as routes
from app.core.security import COOKIE_NAME
from app.db.session import get_db
from app.models import MatchEvent, User
from app.models.foundation import utc_now
from app.services.event_stream import EventBroker
from app.worker.clips import ClipManager, Recording
from fastapi import FastAPI
from sqlalchemy.orm import Session
from test_events import event_context as _event_fixture
from test_events import grant

event_context = _event_fixture


def frames(manager, recording, origin, offsets):
    for i, offset in enumerate(offsets):
        frame = SimpleNamespace(
            image=np.full((120, 160, 3), i % 255, np.uint8),
            captured_at=datetime.fromtimestamp(origin + offset, UTC).isoformat(),
        )
        manager.append(recording, frame)


def test_real_ffmpeg_shared_segments_cover_before_after_and_partial(event_context):
    c = event_context
    manager = ClipManager(c.settings, c.store, start=False)
    recording = Recording(c.settings.clip_buffer_dir / ("a" * 32))
    manager.recordings[(1, c.candidate.stream_session_id)] = recording
    origin = datetime.now(UTC).timestamp()
    frames(manager, recording, origin, [i / 10 for i in range(-50, 101)])
    name, details = manager.assemble(recording, origin)
    assert not details["partial"]
    assert details["before_seconds"] == 5 and details["after_seconds"] == 10
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(c.store.clip_path(name)),
        ],
        capture_output=True,
        check=True,
        text=True,
    )
    assert float(json.loads(probe.stdout)["format"]["duration"]) == pytest.approx(15.1, abs=0.15)
    second, partial = manager.assemble(recording, origin + 8)
    assert second != name and partial["partial"]
    assert recording.pins == 0
    assert len(list(recording.directory.glob("*.mjpg"))) <= 17
    manager.close()


def test_storage_limit_and_delete_completion_race(event_context):
    c = event_context
    manager = ClipManager(c.settings, c.store, start=False)
    recording = Recording(c.settings.clip_buffer_dir / ("a" * 32))
    manager.recordings[(1, c.candidate.stream_session_id)] = recording
    c.settings.storage_min_free_bytes = 2**60
    frames(manager, recording, datetime.now(UTC).timestamp(), [0])
    assert not recording.frames and recording.error == "storage_limit"
    event_id = c.store.record(c.candidate)
    with Session(c.engine) as db:
        admin = db.get(User, 1)
        c.store.delete([event_id], admin)
    assert not c.store.finish_clip(event_id, name="b" * 32 + ".mp4")
    manager.close()


def test_clip_authenticated_range_journal_retention_and_revocation(event_context):
    c = event_context
    event_id = c.store.record(c.candidate)
    name = "b" * 32 + ".mp4"
    c.settings.clip_dir.mkdir()
    c.store.clip_path(name).write_bytes(b"0123456789")
    assert c.store.finish_clip(event_id, name=name, details={"partial": True})
    with Session(c.engine) as db:
        row = db.get(MatchEvent, event_id)
        assert row.clip_state == "ready" and row.change_id == 2
    app = FastAPI()
    app.state.settings, app.state.engine = c.settings, c.engine
    app.state.events, app.state.event_broker = c.store, EventBroker(c.store)
    app.include_router(routes.router)

    async def database():
        with Session(c.engine) as db:
            yield db

    app.dependency_overrides[get_db] = database

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            url = f"/api/events/{event_id}/clip"
            assert (await client.get(url)).status_code == 401
            client.cookies.set(COOKIE_NAME, "token-3")
            assert (await client.get(url)).status_code == 404
            grant(c, user_id=3)
            result = await client.get(url, headers={"Range": "bytes=2-5"})
            assert result.status_code == 206 and result.content == b"2345"
            assert result.headers["cache-control"] == "no-store"
            assert (await client.head(url)).status_code == 200
            with Session(c.engine) as db:
                db.get(MatchEvent, event_id).clip_expires_at = utc_now() - timedelta(seconds=1)
                db.commit()
            assert (await client.get(url)).status_code == 404
            c.store.cleanup()
            assert not c.store.clip_path(name).exists()
            with Session(c.engine) as db:
                row = db.get(MatchEvent, event_id)
                assert row.face_image_path and row.clip_state == "expired"

    asyncio.run(run())
