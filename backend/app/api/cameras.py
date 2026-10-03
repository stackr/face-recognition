from datetime import UTC

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import admin_user, current_user
from app.core.security import encrypt_rtsp, redact_rtsp
from app.db.session import get_db
from app.models import AuditLog, Camera, CameraPermission, User
from app.schemas.foundation import CameraInput, CameraOutput
from app.services.camera_operations import camera_mutation

router = APIRouter(prefix="/api/cameras", tags=["Cameras"])


def output(camera: Camera, request: Request, db: Session, user: User) -> CameraOutput:
    key = request.app.state.settings.rtsp_encryption_key.get_secret_value()
    grant = db.get(CameraPermission, (camera.id, user.id)) if user.role != "admin" else None
    return CameraOutput(
        camera_id=camera.id,
        name=camera.name,
        description=camera.description,
        rtsp_url=redact_rtsp(camera.rtsp_url_encrypted, key)
        if camera.source_type == "rtsp"
        else "",
        source_type=camera.source_type,
        has_test_video=bool(camera.video_path),
        location=camera.location,
        enabled=camera.enabled,
        can_view=user.role == "admin" or grant is not None,
        can_operate=user.role == "admin"
        or (user.role == "operator" and grant is not None and grant.can_operate),
        created_at=camera.created_at.replace(tzinfo=UTC),
        updated_at=camera.updated_at.replace(tzinfo=UTC),
    )


def find_camera(camera_id: int, db: Session) -> Camera:
    camera = db.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(404, "Camera not found")
    return camera


def save(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Camera name already exists") from None


@router.get("", response_model=list[CameraOutput])
def list_cameras(
    request: Request,
    offset: int = 0,
    limit: int = 100,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if offset < 0 or not 1 <= limit <= 200:
        raise HTTPException(422, "Invalid pagination")
    rows = db.scalars(select(Camera).order_by(Camera.id).offset(offset).limit(limit)).all()
    return [output(camera, request, db, user) for camera in rows]


@router.get("/{camera_id}", response_model=CameraOutput)
def get_camera(
    camera_id: int,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    return output(find_camera(camera_id, db), request, db, user)


@router.post("", response_model=CameraOutput, status_code=201)
def create_camera(
    payload: CameraInput,
    request: Request,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    key = request.app.state.settings.rtsp_encryption_key.get_secret_value()
    camera = Camera(
        name=payload.name,
        description=payload.description,
        location=payload.location,
        enabled=payload.enabled,
        source_type=payload.source_type,
        rtsp_url_encrypted=encrypt_rtsp(payload.rtsp_url.get_secret_value(), key),
    )
    db.add(camera)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Camera name already exists") from None
    db.add(
        AuditLog(
            user_id=user.id,
            action="camera.create",
            resource_type="camera",
            resource_id=str(camera.id),
        )
    )
    save(db)
    db.refresh(camera)
    return output(camera, request, db, user)


@router.put("/{camera_id}", response_model=CameraOutput, dependencies=[Depends(camera_mutation)])
def update_camera(
    camera_id: int,
    payload: CameraInput,
    request: Request,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    camera = find_camera(camera_id, db)
    request.app.state.worker.stop(camera_id)
    camera.name, camera.description = payload.name, payload.description
    camera.location, camera.enabled = payload.location, payload.enabled
    camera.source_type = payload.source_type
    camera.rtsp_url_encrypted = encrypt_rtsp(
        payload.rtsp_url.get_secret_value(),
        request.app.state.settings.rtsp_encryption_key.get_secret_value(),
    )
    db.add(
        AuditLog(
            user_id=user.id,
            action="camera.update",
            resource_type="camera",
            resource_id=str(camera.id),
        )
    )
    save(db)
    db.refresh(camera)
    return output(camera, request, db, user)


@router.delete("/{camera_id}", status_code=204, dependencies=[Depends(camera_mutation)])
def delete_camera(
    camera_id: int,
    request: Request,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    camera = find_camera(camera_id, db)
    request.app.state.worker.stop(camera_id)
    path = camera.video_path
    db.delete(camera)
    db.add(
        AuditLog(
            user_id=user.id,
            action="camera.delete",
            resource_type="camera",
            resource_id=str(camera_id),
        )
    )
    save(db)
    if path:
        directory = request.app.state.settings.video_dir.resolve()
        video = (directory / path).resolve()
        if video.parent == directory:
            video.unlink(missing_ok=True)
