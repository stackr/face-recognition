from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.foundation import Base, utc_now


class Person(Base):
    __tablename__ = "persons"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    deleting: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class PersonFace(Base):
    __tablename__ = "person_faces"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id", ondelete="CASCADE"), index=True)
    image_path: Mapped[str | None] = mapped_column(String(64))
    embedding_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    embedding_encrypted: Mapped[str | None] = mapped_column(Text)
    model_version: Mapped[str] = mapped_column(String(100), nullable=False)
    quality: Mapped[float] = mapped_column(Float, nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    image_expires_at: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    embedding_expires_at: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class PersonPermission(Base):
    __tablename__ = "person_permissions"
    person_id: Mapped[int] = mapped_column(
        ForeignKey("persons.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )


class GalleryState(Base):
    __tablename__ = "face_gallery_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class FaceCleanupJob(Base):
    __tablename__ = "face_cleanup_jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    person_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    face_id: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(80))
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
