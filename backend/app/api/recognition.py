import threading
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api.analysis import camera_access
from app.api.dependencies import admin_user, current_user
from app.db.session import get_db
from app.models import AuditLog, Camera, CameraPermission, FunctionSettings, RecognitionLog, User
from app.models.foundation import utc_now
from app.schemas.recognition import SamplingSettings, SamplingUpdate, sample_window
from app.services.recognition import load_sampling

router = APIRouter(tags=["Recognition controls"])
settings_lock = threading.Lock()
OUTCOMES = {
    "insufficient_consensus",
    "no_face",
    "ambiguous",
    "quality_rejected",
    "collecting_samples",
    "matched",
    "below_threshold",
    "gallery_empty",
    "search_unavailable",
}


def settings_output(request, revision, values):
    try:
        active = request.app.state.worker.request("GET", "/internal/settings", timeout=2).json()
    except HTTPException:
        active = None
    return {
        "revision": revision,
        "values": values.model_dump(),
        "active": active,
        "applied": bool(
            active
            and active.get("revision") == revision
            and active.get("values") == values.model_dump()
        ),
        "features": {
            "head_detection": True,
            "retry_detector_size": 640,
            "sample_count": request.app.state.settings.face_sample_count,
            "sample_window_seconds": sample_window(
                request.app.state.settings, values.face_analysis_interval
            ),
            "minimum_samples": 2,
            "person_track_start_threshold": request.app.state.settings.new_track_threshold,
        },
    }


@router.get("/api/function-settings")
def get_settings(
    request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    revision, values = load_sampling(db, request.app.state.settings)
    return settings_output(request, revision, values)


@router.put("/api/function-settings")
def update_settings(
    payload: SamplingUpdate,
    request: Request,
    response: Response,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    with settings_lock:
        row = db.scalar(select(FunctionSettings).where(FunctionSettings.id == 1).with_for_update())
        if payload.revision != (row.revision if row else 0):
            raise HTTPException(409, "Settings changed; reload before saving")
        _, current = load_sampling(db, request.app.state.settings)
        values = SamplingSettings.model_validate(
            current.model_dump() | payload.model_dump(exclude={"revision"}, exclude_unset=True)
        )
        if row is None:
            row = FunctionSettings(id=1, revision=0, values={})
            db.add(row)
        row.revision += 1
        row.values, row.updated_at = values.model_dump(), utc_now()
        db.add(
            AuditLog(
                user_id=user.id,
                action="function_settings.update",
                resource_type="function_settings",
                resource_id=str(row.revision),
            )
        )
        db.commit()
        revision = row.revision
    try:
        request.app.state.worker.request("POST", "/internal/settings/reload", json={}, timeout=3)
    except HTTPException:
        pass
    result = settings_output(request, revision, values)
    if not result["applied"]:
        response.status_code = 202
    return result


def as_utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@router.delete("/api/recognition-logs")
def delete_logs(user: User = Depends(admin_user), db: Session = Depends(get_db)):
    result = db.execute(delete(RecognitionLog))
    db.add(
        AuditLog(
            user_id=user.id,
            action="recognition_logs.delete_all",
            resource_type="recognition_log",
            resource_id="all",
        )
    )
    db.commit()
    return {"deleted_count": result.rowcount}


@router.get("/api/recognition-logs")
def list_logs(
    request: Request,
    camera_id: int | None = Query(default=None, ge=1),
    outcome: str | None = None,
    reason: str | None = Query(default=None, max_length=40),
    start: datetime | None = None,
    end: datetime | None = None,
    before_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if outcome is not None and outcome not in OUTCOMES:
        raise HTTPException(422, "Invalid outcome")
    if start and end and as_utc(start) >= as_utc(end):
        raise HTTPException(422, "End must follow start")
    scope = [
        RecognitionLog.captured_at
        >= utc_now() - timedelta(days=request.app.state.settings.recognition_log_retention_days)
    ]
    if user.role != "admin":
        scope += [
            Camera.enabled.is_(True),
            Camera.id.in_(
                select(CameraPermission.camera_id).where(CameraPermission.user_id == user.id)
            ),
        ]
    if camera_id is not None:
        camera_access(db, user, camera_id)
        scope.append(RecognitionLog.camera_id == camera_id)
    if outcome:
        scope.append(RecognitionLog.outcome == outcome)
    if reason:
        scope.append(RecognitionLog.primary_reason == reason)
    if start:
        scope.append(RecognitionLog.captured_at >= as_utc(start).replace(tzinfo=None))
    if end:
        scope.append(RecognitionLog.captured_at < as_utc(end).replace(tzinfo=None))
    base = select(RecognitionLog).join(Camera, Camera.id == RecognitionLog.camera_id).where(*scope)
    counts = dict(
        db.execute(
            base.with_only_columns(RecognitionLog.outcome, func.count()).group_by(
                RecognitionLog.outcome
            )
        ).all()
    )
    reason_counts = dict(
        db.execute(
            base.with_only_columns(RecognitionLog.primary_reason, func.count())
            .where(RecognitionLog.primary_reason.is_not(None))
            .group_by(RecognitionLog.primary_reason)
        ).all()
    )
    rows = db.execute(
        base.with_only_columns(RecognitionLog, Camera.name)
        .where(RecognitionLog.id < before_id if before_id is not None else True)
        .order_by(RecognitionLog.id.desc())
        .limit(limit + 1)
    ).all()
    items = [
        {
            "id": row.id,
            "camera_id": row.camera_id,
            "camera_name": name,
            "stream_session_id": row.stream_session_id,
            "track_id": row.track_id,
            "frame_id": row.frame_id,
            "captured_at": row.captured_at.replace(tzinfo=UTC),
            "outcome": row.outcome,
            "reasons": row.reasons,
            "metrics": row.metrics,
            "quality": row.quality,
            "top_similarity": row.top_similarity,
            "sample_count": row.sample_count,
            "settings_revision": row.settings_revision,
        }
        for row, name in rows[:limit]
    ]
    return {
        "items": items,
        "has_more": len(rows) > limit,
        "next_cursor": items[-1]["id"] if len(rows) > limit else None,
        "summary": {"total": sum(counts.values()), "outcomes": counts, "reasons": reason_counts},
        "retention_days": request.app.state.settings.recognition_log_retention_days,
        "max_records": request.app.state.settings.recognition_log_max_records,
    }
