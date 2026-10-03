"""Persisted candidates and an ordered, recoverable change journal."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.foundation import Base, utc_now


class EventState(Base):
    __tablename__ = "event_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_event_id: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Track(Base):
    __tablename__ = "tracks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"))
    stream_session_id: Mapped[str] = mapped_column(String(32), nullable=False)
    track_id: Mapped[int] = mapped_column(Integer, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    __table_args__ = (
        UniqueConstraint("camera_id", "stream_session_id", "track_id", name="uq_track_session"),
    )


class MatchEvent(Base):
    __tablename__ = "match_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    person_id: Mapped[int | None] = mapped_column(
        ForeignKey("persons.id", ondelete="SET NULL"), index=True
    )
    camera_id: Mapped[int | None] = mapped_column(
        ForeignKey("cameras.id", ondelete="SET NULL"), index=True
    )
    track_pk: Mapped[int | None] = mapped_column(ForeignKey("tracks.id", ondelete="SET NULL"))
    stream_session_id: Mapped[str] = mapped_column(String(32), nullable=False)
    track_id: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    face_similarity: Mapped[float] = mapped_column(Float, nullable=False)
    face_quality: Mapped[float] = mapped_column(Float, nullable=False)
    face_image_path: Mapped[str | None] = mapped_column(String(64))
    frame_image_path: Mapped[str | None] = mapped_column(String(64))
    video_clip_path: Mapped[str | None] = mapped_column(String(64))
    clip_state: Mapped[str] = mapped_column(String(16), default="disabled", nullable=False)
    clip_error: Mapped[str | None] = mapped_column(String(40))
    clip_details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    clip_expires_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(16), default="candidate", nullable=False)
    change_id: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    image_expires_at: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "camera_id", "stream_session_id", "track_id", "person_id", name="uq_event_candidate"
        ),
    )


class EventChange(Base):
    __tablename__ = "event_changes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("match_events.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
