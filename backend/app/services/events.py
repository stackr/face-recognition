"""SQL is authoritative; files are private and every delivery rechecks permissions."""

import logging
import os
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.face_data import MODEL_VERSION
from app.models import (
    AuditLog,
    Camera,
    CameraPermission,
    EventChange,
    EventState,
    GalleryState,
    MatchEvent,
    Person,
    PersonFace,
    PersonPermission,
    Track,
    User,
)
from app.models.foundation import utc_now


@dataclass(frozen=True)
class Candidate:
    camera_id: int
    stream_session_id: str
    track_id: int
    person_id: int
    reference_face_id: int
    gallery_revision: int
    timestamp: str
    similarity: float
    quality: float
    face_jpeg: bytes
    frame_jpeg: bytes


def visible(query, user):
    query = (
        query.join(Camera, Camera.id == MatchEvent.camera_id)
        .join(Person, Person.id == MatchEvent.person_id)
        .where(Person.deleting.is_(False), MatchEvent.expires_at > utc_now())
    )
    if user.role != "admin":
        query = query.where(
            select(CameraPermission.camera_id)
            .where(
                CameraPermission.camera_id == MatchEvent.camera_id,
                CameraPermission.user_id == user.id,
            )
            .exists(),
            select(PersonPermission.person_id)
            .where(
                PersonPermission.person_id == MatchEvent.person_id,
                PersonPermission.user_id == user.id,
            )
            .exists(),
        )
    return query


def event_output(db, event, user):
    grant = db.get(CameraPermission, (event.camera_id, user.id))
    images = event.image_expires_at > utc_now()
    return {
        "type": "person_match",
        "event_id": event.id,
        "change_id": event.change_id,
        "camera_id": event.camera_id,
        "camera_name": db.get(Camera, event.camera_id).name,
        "person_id": event.person_id,
        "person_name": db.get(Person, event.person_id).name,
        "stream_session_id": event.stream_session_id,
        "track_id": event.track_id,
        "timestamp": event.timestamp.isoformat() + "Z",
        "updated_at": event.updated_at.isoformat() + "Z",
        "face_similarity": event.face_similarity,
        "face_quality": event.face_quality,
        "status": event.status,
        "thumbnail_url": f"/api/events/{event.id}/face"
        if images and event.face_image_path
        else None,
        "frame_url": f"/api/events/{event.id}/frame" if images and event.frame_image_path else None,
        "video_clip_url": None,
        "can_review": user.role == "admin"
        or (user.role == "operator" and grant is not None and grant.can_operate),
    }


class EventStore:
    def __init__(self, settings, engine):
        self.settings, self.engine = settings, engine
        self.lock = threading.RLock()

    def state(self, db, *, lock=False):
        query = select(EventState).where(EventState.id == 1)
        row = db.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise RuntimeError("Event migration required")
        return row

    def path(self, name):
        if not name or not re.fullmatch(r"[a-f0-9]{32}\.jpg", name):
            raise ValueError("Invalid event image path")
        path = self.settings.event_dir / name
        if path.is_symlink() or path.resolve().parent != self.settings.event_dir.resolve():
            raise ValueError("Invalid event image path")
        return path

    def write_image(self, jpeg):
        name = uuid.uuid4().hex + ".jpg"
        directory = self.settings.event_dir
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(directory, 0o700)
        path = self.path(name)
        try:
            with path.open("xb") as stream:
                os.chmod(path, 0o600)
                stream.write(jpeg)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return name

    def remove_files(self, names):
        for name in names:
            if name:
                try:
                    self.path(name).unlink(missing_ok=True)
                except OSError:
                    logging.getLogger("cctv.events").warning("Event image cleanup pending")

    def record(self, candidate):
        if (
            not re.fullmatch(r"[a-f0-9]{32}", candidate.stream_session_id)
            or candidate.track_id < 1
            or not self.settings.face_match_threshold <= candidate.similarity <= 1
            or not self.settings.face_quality_threshold <= candidate.quality <= 1
            or not candidate.face_jpeg.startswith(b"\xff\xd8")
            or not candidate.frame_jpeg.startswith(b"\xff\xd8")
            or len(candidate.face_jpeg) > 200_000
            or len(candidate.frame_jpeg) > 1_500_000
        ):
            raise ValueError("Invalid event candidate")
        timestamp = datetime.fromisoformat(candidate.timestamp)
        if timestamp.tzinfo is None:
            raise ValueError("Candidate timestamp must have timezone")
        timestamp = timestamp.astimezone(UTC).replace(tzinfo=None)
        written, replaced = [], []
        commit_attempted = False
        with self.lock, Session(self.engine) as db:
            try:
                # All writers take this row lock BEFORE allocating IDs. Commit order
                # matches cursor order, including writes from separate processes.
                state = self.state(db, lock=True)
                person, camera = (
                    db.scalar(
                        select(Person).where(Person.id == candidate.person_id).with_for_update()
                    ),
                    db.scalar(
                        select(Camera).where(Camera.id == candidate.camera_id).with_for_update()
                    ),
                )
                reference, gallery = (
                    db.scalar(
                        select(PersonFace)
                        .where(PersonFace.id == candidate.reference_face_id)
                        .with_for_update()
                    ),
                    db.scalar(select(GalleryState).where(GalleryState.id == 1).with_for_update()),
                )
                now = utc_now()
                if (
                    not person
                    or not person.enabled
                    or person.deleting
                    or not camera
                    or not camera.enabled
                    or not reference
                    or not gallery
                    or gallery.revision != candidate.gallery_revision
                    or reference.person_id != person.id
                    or reference.state != "ready"
                    or reference.model_version != MODEL_VERSION
                    or reference.embedding_encrypted is None
                    or reference.embedding_expires_at <= now
                ):
                    return None
                event = db.scalar(
                    select(MatchEvent).where(
                        MatchEvent.camera_id == candidate.camera_id,
                        MatchEvent.stream_session_id == candidate.stream_session_id,
                        MatchEvent.track_id == candidate.track_id,
                        MatchEvent.person_id == candidate.person_id,
                    )
                )
                if event:
                    improved = (candidate.quality, candidate.similarity) > (
                        event.face_quality,
                        event.face_similarity,
                    )
                    if (
                        event.status != "candidate"
                        or not improved
                        or event.expires_at <= now
                        or event.image_expires_at <= now
                        or (now - event.updated_at).total_seconds() < self.settings.event_cooldown
                    ):
                        return None
                    replaced = [event.face_image_path, event.frame_image_path]
                else:
                    if (
                        db.scalar(select(func.count()).select_from(MatchEvent))
                        >= self.settings.event_max_records
                    ):
                        raise OverflowError("Event record limit reached")
                    track = db.scalar(
                        select(Track).where(
                            Track.camera_id == candidate.camera_id,
                            Track.stream_session_id == candidate.stream_session_id,
                            Track.track_id == candidate.track_id,
                        )
                    )
                    if track is None:
                        track = Track(
                            camera_id=candidate.camera_id,
                            stream_session_id=candidate.stream_session_id,
                            track_id=candidate.track_id,
                            first_seen_at=timestamp,
                            last_seen_at=timestamp,
                        )
                        db.add(track)
                        db.flush()
                    state.last_event_id += 1
                    event = MatchEvent(
                        id=state.last_event_id,
                        camera_id=candidate.camera_id,
                        person_id=candidate.person_id,
                        track_pk=track.id,
                        stream_session_id=candidate.stream_session_id,
                        track_id=candidate.track_id,
                        timestamp=timestamp,
                        status="candidate",
                        expires_at=now + timedelta(days=self.settings.event_retention_days),
                        image_expires_at=now
                        + timedelta(days=self.settings.event_image_retention_days),
                    )
                    db.add(event)
                used = sum(p.stat().st_size for p in self.settings.event_dir.glob("*.jpg"))
                if (
                    used + len(candidate.face_jpeg) + len(candidate.frame_jpeg)
                    > self.settings.event_storage_max_mb * 1024 * 1024
                ):
                    raise OverflowError("Event image storage limit reached")
                for jpeg in (candidate.face_jpeg, candidate.frame_jpeg):
                    written.append(self.write_image(jpeg))
                event.face_image_path, event.frame_image_path = written
                event.face_quality, event.face_similarity = candidate.quality, candidate.similarity
                event.updated_at = now
                state.revision += 1
                event.change_id = state.revision
                db.flush()
                db.add(EventChange(id=state.revision, event_id=event.id))
                event_id = event.id
                commit_attempted = True
                db.commit()
            except Exception:
                db.rollback()
                # A lost COMMIT response has an ambiguous outcome. Keep its files
                # until SQL-backed orphan cleanup can safely decide what survived.
                if not commit_attempted:
                    self.remove_files(written)
                raise
        self.remove_files(replaced)
        return event_id

    def page(self, db, user, *, after_id=0, after_change_id=None, limit=50, latest=False):
        state = self.state(db)
        highwater = state.revision if after_change_id is not None else state.last_event_id
        if latest:
            rows = list(
                db.scalars(
                    visible(select(MatchEvent), user)
                    .where(MatchEvent.id <= highwater)
                    .order_by(MatchEvent.id.desc())
                    .limit(limit)
                )
            )
            return {
                "items": [event_output(db, row, user) for row in rows],
                "next_cursor": highwater,
                "change_cursor": state.revision,
                "has_more": False,
                "cursor_kind": "event_id",
            }
        if after_change_id is not None:
            query = (
                visible(
                    select(EventChange, MatchEvent).join(
                        MatchEvent, MatchEvent.id == EventChange.event_id
                    ),
                    user,
                )
                .where(EventChange.id > after_change_id, EventChange.id <= highwater)
                .order_by(EventChange.id)
            )
            rows = db.execute(query.limit(limit + 1)).all()
            items = [event_output(db, row[1], user) for row in rows[:limit]]
            cursor = rows[limit - 1][0].id if len(rows) > limit else highwater
        else:
            query = (
                visible(select(MatchEvent), user)
                .where(MatchEvent.id > after_id, MatchEvent.id <= highwater)
                .order_by(MatchEvent.id)
            )
            rows = list(db.scalars(query.limit(limit + 1)))
            items = [event_output(db, event, user) for event in rows[:limit]]
            cursor = rows[limit - 1].id if len(rows) > limit else highwater
        return {
            "items": items,
            "next_cursor": cursor,
            "has_more": len(rows) > limit,
            "cursor_kind": "change_id" if after_change_id is not None else "event_id",
        }

    def find(self, db, user, event_id):
        event = db.scalar(visible(select(MatchEvent), user).where(MatchEvent.id == event_id))
        if event is None:
            raise HTTPException(404, "Event unavailable")
        return event

    def review(self, event_id, user, status):
        with self.lock, Session(self.engine) as db:
            state = self.state(db, lock=True)
            user = db.get(User, user.id)
            if user is None or not user.enabled:
                raise HTTPException(401, "Account unavailable")
            event = self.find(db, user, event_id)
            grant = db.get(CameraPermission, (event.camera_id, user.id))
            if user.role != "admin" and (
                user.role != "operator" or not grant or not grant.can_operate
            ):
                raise HTTPException(403, "Event review permission required")
            if event.status != status:
                event.status, event.updated_at = status, utc_now()
                state.revision += 1
                event.change_id = state.revision
                db.add(EventChange(id=state.revision, event_id=event.id))
                db.add(
                    AuditLog(
                        user_id=user.id,
                        action=f"event.{status}",
                        resource_type="event",
                        resource_id=str(event.id),
                    )
                )
                db.commit()
            return event_output(db, event, user)

    def cleanup(self):
        with self.lock, Session(self.engine) as db:
            self.state(db, lock=True)
            now = utc_now()
            deleting = set(db.scalars(select(Person.id).where(Person.deleting.is_(True))))
            removed = []
            rows = list(
                db.scalars(
                    select(MatchEvent)
                    .where(
                        (MatchEvent.expires_at <= now)
                        | MatchEvent.person_id.is_(None)
                        | MatchEvent.camera_id.is_(None)
                        | MatchEvent.person_id.in_(
                            select(Person.id).where(Person.deleting.is_(True))
                        )
                        | (
                            (MatchEvent.image_expires_at <= now)
                            & (
                                MatchEvent.face_image_path.is_not(None)
                                | MatchEvent.frame_image_path.is_not(None)
                            )
                        )
                    )
                    .limit(200)
                )
            )
            for event in rows:
                removed.extend([event.face_image_path, event.frame_image_path])
                event.face_image_path = event.frame_image_path = None
                if (
                    event.expires_at <= now
                    or event.person_id is None
                    or event.camera_id is None
                    or event.person_id in deleting
                ):
                    db.delete(event)
            db.execute(
                delete(Track).where(
                    ~select(MatchEvent.id).where(MatchEvent.track_pk == Track.id).exists(),
                    Track.last_seen_at < now - timedelta(days=self.settings.event_retention_days),
                )
            )
            db.commit()
            self.remove_files(removed)
            known = set(db.scalars(select(MatchEvent.face_image_path))) | set(
                db.scalars(select(MatchEvent.frame_image_path))
            )
            cutoff = (now - timedelta(hours=1)).replace(tzinfo=UTC).timestamp()
            for path in self.settings.event_dir.glob("*.jpg"):
                if path.name not in known and path.stat().st_mtime < cutoff:
                    self.remove_files([path.name])
