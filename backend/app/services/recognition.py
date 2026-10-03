"""Diagnostics contain measurements and track IDs, never images or embeddings."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import Camera, FunctionSettings, RecognitionLog
from app.models.foundation import utc_now
from app.schemas.recognition import SamplingSettings


def load_sampling(db, settings):
    row = db.get(FunctionSettings, 1)
    # Older saved JSON has only the three sampling controls. Keep those values
    # and supply newly introduced controls from the environment defaults.
    values = SamplingSettings.defaults(settings).model_dump() | (row.values if row else {})
    return row.revision if row else 0, SamplingSettings.model_validate(values)


class RecognitionStore:
    def __init__(self, settings, engine):
        self.settings, self.engine = settings, engine

    def save(self, observations):
        with Session(self.engine) as db:
            allowed = set(
                db.scalars(
                    select(Camera.id).where(
                        Camera.id.in_({item["camera_id"] for item in observations})
                    )
                )
            )
            fields = ("camera_id", "stream_session_id", "track_id", "frame_id")
            existing = set(
                db.execute(
                    select(*(getattr(RecognitionLog, field) for field in fields)).where(
                        RecognitionLog.camera_id.in_(allowed),
                        RecognitionLog.stream_session_id.in_(
                            {item["stream_session_id"] for item in observations}
                        ),
                        RecognitionLog.frame_id.in_({item["frame_id"] for item in observations}),
                    )
                ).all()
            )
            for item in observations:
                key = tuple(item[field] for field in fields)
                if item["camera_id"] in allowed and key not in existing:
                    db.add(RecognitionLog(**item))
                    existing.add(key)
            db.flush()
            self.cleanup_db(db)
            db.commit()

    def cleanup(self):
        with Session(self.engine) as db:
            self.cleanup_db(db)
            db.commit()

    def cleanup_db(self, db):
        db.execute(
            delete(RecognitionLog).where(
                RecognitionLog.captured_at
                < utc_now() - timedelta(days=self.settings.recognition_log_retention_days)
            )
        )
        count = db.scalar(select(func.count()).select_from(RecognitionLog))
        excess = count - self.settings.recognition_log_max_records
        if excess > 0:
            boundary = db.scalar(
                select(RecognitionLog.id).order_by(RecognitionLog.id).offset(excess - 1).limit(1)
            )
            db.execute(delete(RecognitionLog).where(RecognitionLog.id <= boundary))


def observation(camera_id, session, frame_id, captured_at, track, revision, search_status):
    face = track.get("face", {})
    if face.get("frame_id") != frame_id:
        return None
    reasons = list(face.get("reasons", []))
    status = face.get("status")
    # A rejected current frame was not embedded; earlier cached scores are unrelated.
    comparison = face.get("comparison", {}) if status == "accepted" else {}
    outcome = {"rejected": "quality_rejected"}.get(status, status)
    if status == "accepted":
        outcome = (
            comparison.get("outcome", "search_unavailable")
            if search_status == "ready"
            else "search_unavailable"
        )
    metric_keys = (
        "face_size",
        "blur_score",
        "brightness",
        "yaw",
        "pitch",
        "roll",
        "confidence",
        "detector_region",
        "detector_input",
        "detection_passes",
    )
    when = (
        datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        .astimezone(UTC)
        .replace(tzinfo=None)
    )
    return {
        "camera_id": camera_id,
        "stream_session_id": session,
        "track_id": track["track_id"],
        "frame_id": frame_id,
        "captured_at": when,
        "outcome": outcome,
        "primary_reason": reasons[0] if reasons else None,
        "reasons": reasons,
        "metrics": {key: face[key] for key in metric_keys if key in face}
        | {
            key: comparison.get(key)
            for key in ("threshold", "supporting_samples", "minimum_samples")
        },
        "quality": face.get("quality", 0),
        "top_similarity": comparison.get("top_similarity"),
        "sample_count": face.get("sample_count", 0),
        "settings_revision": revision,
    }
