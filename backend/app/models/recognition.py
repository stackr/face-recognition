from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.foundation import Base, utc_now


class FunctionSettings(Base):
    __tablename__ = "function_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    values: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class RecognitionLog(Base):
    __tablename__ = "recognition_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"))
    stream_session_id: Mapped[str] = mapped_column(String(32), nullable=False)
    track_id: Mapped[int] = mapped_column(Integer, nullable=False)
    frame_id: Mapped[int] = mapped_column(Integer, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    primary_reason: Mapped[str | None] = mapped_column(String(40))
    reasons: Mapped[list] = mapped_column(JSON, nullable=False)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False)
    quality: Mapped[float] = mapped_column(Float, nullable=False)
    top_similarity: Mapped[float | None] = mapped_column(Float)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    settings_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "camera_id",
            "stream_session_id",
            "track_id",
            "frame_id",
            name="uq_recognition_observation",
        ),
        Index("ix_recognition_camera_id_id", "camera_id", "id"),
        Index("ix_recognition_captured_at", "captured_at"),
        Index("ix_recognition_outcome_id", "outcome", "id"),
    )
