import asyncio
import os
import shutil
import time
import uuid
from typing import Literal

import httpx
from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.cameras import find_camera, output, save
from app.api.dependencies import admin_user, current_user, operator_user
from app.api.persons import filter_camera_status
from app.db.session import get_db
from app.models import AuditLog, CameraPermission, User
from app.services.camera_operations import camera_mutation

router = APIRouter(prefix="/api/cameras", tags=["Analysis"])


def camera_access(db, user, camera_id, *, operate=False):
    if user.role == "admin":
        return
    grant = db.get(CameraPermission, (camera_id, user.id))
    if grant is None or (operate and not grant.can_operate):
        raise HTTPException(403, "Camera permission required")


class AccessInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str
    can_view: bool = True
    can_operate: bool = False


@router.put("/{camera_id}/access", dependencies=[Depends(camera_mutation)])
def set_access(
    camera_id: int,
    payload: AccessInput,
    request: Request,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    find_camera(camera_id, db)
    target = db.scalar(
        select(User).where(User.username == payload.username, User.enabled.is_(True))
    )
    if target is None:
        raise HTTPException(404, "Account not found")
    if payload.can_operate and (not payload.can_view or target.role != "operator"):
        raise HTTPException(422, "Only operators with view access can operate")
    grant = db.get(CameraPermission, (camera_id, target.id))
    if payload.can_view:
        if grant is None:
            grant = CameraPermission(camera_id=camera_id, user_id=target.id)
            db.add(grant)
        grant.can_operate = payload.can_operate
    elif grant is not None:
        db.delete(grant)
    audit(db, user, camera_id, "camera.access_update")
    return {
        "username": target.username,
        "can_view": payload.can_view,
        "can_operate": payload.can_operate,
    }


class StartInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_type: Literal["rtsp", "mp4"] | None = None
    loop: bool = True


def audit(db, user, camera_id, action):
    db.add(
        AuditLog(user_id=user.id, action=action, resource_type="camera", resource_id=str(camera_id))
    )
    save(db)


@router.get("/{camera_id}/status")
def status(
    camera_id: int,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    find_camera(camera_id, db)
    camera_access(db, user, camera_id)
    return filter_camera_status(db, user, request.app.state.worker.status(camera_id))


@router.post("/{camera_id}/start", dependencies=[Depends(camera_mutation)])
def start(
    camera_id: int,
    payload: StartInput,
    request: Request,
    user: User = Depends(operator_user),
    db: Session = Depends(get_db),
):
    camera = find_camera(camera_id, db)
    camera_access(db, user, camera_id, operate=True)
    if not camera.enabled:
        raise HTTPException(409, "Camera disabled")
    settings = request.app.state.settings
    source_type = payload.source_type or camera.source_type
    if source_type == "mp4":
        if not camera.video_path:
            raise HTTPException(409, "Upload a test MP4 first")
        source = str((settings.video_dir / camera.video_path).resolve())
    else:
        if camera.source_type != "rtsp":
            raise HTTPException(409, "Camera has no RTSP source")
        source = (
            Fernet(settings.rtsp_encryption_key.get_secret_value())
            .decrypt(camera.rtsp_url_encrypted.encode())
            .decode()
        )
    response = request.app.state.worker.request(
        "POST",
        f"/internal/cameras/{camera_id}/start",
        json={"source": source, "source_type": source_type, "loop": payload.loop},
    )
    try:
        audit(db, user, camera_id, "camera.start")
    except Exception:
        request.app.state.worker.stop(camera_id)
        raise
    return response.json()


@router.post("/{camera_id}/stop", dependencies=[Depends(camera_mutation)])
def stop(
    camera_id: int,
    request: Request,
    user: User = Depends(operator_user),
    db: Session = Depends(get_db),
):
    find_camera(camera_id, db)
    camera_access(db, user, camera_id, operate=True)
    result = request.app.state.worker.stop(camera_id)
    audit(db, user, camera_id, "camera.stop")
    return result


@router.put("/{camera_id}/video", dependencies=[Depends(camera_mutation)])
async def upload(
    camera_id: int,
    request: Request,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    camera = find_camera(camera_id, db)
    settings = request.app.state.settings
    if request.headers.get("content-type", "").split(";")[0] != "video/mp4":
        raise HTTPException(415, "Use video/mp4")
    limit = settings.video_upload_max_mb * 2**20
    try:
        length = int(request.headers.get("content-length", "0"))
    except ValueError:
        raise HTTPException(422, "Invalid content length") from None
    if length > limit:
        raise HTTPException(413, "MP4 exceeds upload limit")
    if request.app.state.upload_lock.locked():
        raise HTTPException(429, "Another upload is in progress")
    async with request.app.state.upload_lock:
        state = await run_in_threadpool(request.app.state.worker.status, camera_id)
        if state["state"] in {"opening", "running", "reconnecting", "draining", "stopping"}:
            raise HTTPException(409, "Stop analysis before uploading")
        directory = settings.video_dir.resolve()
        directory.mkdir(parents=True, exist_ok=True)
        used = sum(path.stat().st_size for path in directory.glob("*.mp4"))
        available = min(
            settings.video_storage_max_mb * 2**20 - used,
            shutil.disk_usage(directory).free - 500 * 2**20,
        )
        if available <= 0 or length > available:
            raise HTTPException(413, "Video storage limit reached")
        path = directory / (uuid.uuid4().hex + ".mp4")
        old_path = camera.video_path
        committed = False
        try:
            size = 0
            with path.open("xb") as stream:
                os.chmod(path, 0o600)
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > min(limit, available):
                        raise HTTPException(413, "MP4 exceeds storage or upload limit")
                    stream.write(chunk)
            if size == 0:
                raise HTTPException(422, "Empty video")
            await run_in_threadpool(
                request.app.state.worker.request,
                "POST",
                "/internal/videos/probe",
                json={"source": str(path), "source_type": "mp4"},
            )
            camera.video_path = path.name
            audit(db, user, camera_id, "camera.video_upload")
            committed = True
        finally:
            if not committed:
                path.unlink(missing_ok=True)
        if old_path:
            old = (directory / old_path).resolve()
            if old.parent == directory and old != path:
                old.unlink(missing_ok=True)
        return output(camera, request)


def preview_access(request, camera_id):
    # Use short-lived DB sessions, so each viewer does not retain a pool slot.
    with Session(request.app.state.engine) as db:
        user = current_user(request, db)
        camera = find_camera(camera_id, db)
        camera_access(db, user, camera_id)
        if not camera.enabled:
            raise HTTPException(403, "Camera disabled")


async def preview_frames(request, camera_id, transport=None):
    settings = request.app.state.settings
    last_frame = None
    last_authorized = 0
    idle_since = time.monotonic()
    try:
        async with httpx.AsyncClient(
            base_url=settings.worker_url,
            headers={"X-Service-Token": settings.service_token.get_secret_value()},
            timeout=5,
            trust_env=False,
            transport=transport,
        ) as client:
            while not await request.is_disconnected():
                now = time.monotonic()
                if now - last_authorized >= 2:
                    try:
                        await run_in_threadpool(preview_access, request, camera_id)
                    except HTTPException:
                        break
                    last_authorized = now
                try:
                    response = await client.get(f"/internal/cameras/{camera_id}/frame")
                except httpx.HTTPError:
                    break
                if response.status_code not in {200, 204}:
                    break
                state = response.headers.get("X-Camera-State")
                session = response.headers.get("X-Stream-Session")
                if state in {"stopping", "stopped", "ended", "error", "reconnecting"}:
                    break
                if last_frame is not None and session and session != last_frame[0]:
                    break
                if response.status_code == 200:
                    identifier = (
                        response.headers.get("X-Stream-Session"),
                        response.headers.get("X-Frame-Id"),
                    )
                    if identifier != last_frame:
                        last_frame = identifier
                        idle_since = time.monotonic()
                        captured = response.headers.get("X-Captured-At", "")
                        headers = f"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: {len(response.content)}\r\nX-Stream-Session: {identifier[0]}\r\nX-Frame-Id: {identifier[1]}\r\nX-Captured-At: {captured}\r\n\r\n"
                        # One frame per yield; ASGI backpressure cannot accumulate a queue.
                        yield headers.encode() + response.content + b"\r\n"
                if time.monotonic() - idle_since > 10:
                    break
                await asyncio.sleep(1 / settings.preview_fps)
    finally:
        request.app.state.viewers.release(camera_id)


@router.get("/{camera_id}/preview")
async def preview(camera_id: int, request: Request):
    await run_in_threadpool(preview_access, request, camera_id)
    state = await run_in_threadpool(request.app.state.worker.status, camera_id)
    if state["state"] not in {"opening", "running", "draining", "reconnecting"}:
        raise HTTPException(409, "Start camera analysis first")
    request.app.state.viewers.acquire(camera_id)
    return StreamingResponse(
        preview_frames(request, camera_id),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"},
    )


@router.get("/{camera_id}/faces/{track_id}")
def face_thumbnail(
    camera_id: int,
    track_id: int,
    request: Request,
    stream_session_id: str = Query(pattern=r"^[a-f0-9]{32}$"),
):
    preview_access(request, camera_id)
    response = request.app.state.worker.request(
        "GET",
        f"/internal/cameras/{camera_id}/faces/{track_id}",
        params={"stream_session_id": stream_session_id},
    )
    return Response(
        response.content,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )
