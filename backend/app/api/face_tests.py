"""Authenticated uploads, owner-only results and explicit full history deletion."""

import os
import shutil
import uuid
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from app.api.dependencies import current_user
from app.core.face_test_data import private_directory, storage_size
from app.schemas.face_tests import DEFAULT_MIN_FACE_SIZE

router = APIRouter(prefix="/api/face-tests", tags=["Face detection tests"])


@router.get("")
def list_tests(request: Request, user=Depends(current_user)):
    return request.app.state.worker.request(
        "GET", "/internal/face-tests", params={"owner_id": user.id}
    ).json()


@router.post("")
async def upload(
    request: Request,
    filename: str = Query(default="시험 영상", min_length=1, max_length=200),
    detection_threshold: float | None = Query(default=None, ge=0.1, le=0.99, allow_inf_nan=False),
    min_face_size: int = Query(default=DEFAULT_MIN_FACE_SIZE, ge=8, le=512),
    user=Depends(current_user),
):
    settings = request.app.state.settings
    content_type = request.headers.get("content-type", "").split(";")[0].lower()
    if not content_type.startswith("video/") and content_type != "application/octet-stream":
        raise HTTPException(415, "Video upload required")
    maximum = settings.video_upload_max_mb * 2**20
    try:
        length = int(request.headers.get("content-length", "0"))
    except ValueError:
        raise HTTPException(400, "Invalid upload length") from None
    if length < 0:
        raise HTTPException(400, "Invalid upload length")
    if length > maximum:
        raise HTTPException(413, "Video upload too large")
    lock = request.app.state.face_test_upload_lock
    if lock.locked():
        raise HTTPException(429, "Another video upload is in progress")
    async with lock:
        worker = request.app.state.worker
        existing = await run_in_threadpool(
            worker.request, "GET", "/internal/face-tests", params={"owner_id": user.id}
        )
        if not existing.json()["can_start"]:
            raise HTTPException(429, "Video test limit reached")
        root = private_directory(settings.face_test_dir)
        incoming = private_directory(root / ".incoming")
        initial_size = await run_in_threadpool(storage_size, root)
        storage_max = settings.face_test_storage_max_mb * 2**20
        free_space = shutil.disk_usage(root).free - settings.storage_min_free_bytes
        job_id = str(uuid.uuid4())
        path = incoming / f"{job_id}.video"
        succeeded = False
        try:
            size = 0
            with os.fdopen(
                os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
            ) as stream:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > maximum:
                        raise HTTPException(413, "Video upload too large")
                    if initial_size + size > storage_max or size > free_space:
                        raise HTTPException(429, "Video test storage limit reached")
                    await run_in_threadpool(stream.write, chunk)
            if not size:
                raise HTTPException(422, "Empty video upload")
            filename = filename.replace("\\", "/").rsplit("/", 1)[-1].strip() or "시험 영상"
            response = await run_in_threadpool(
                worker.request,
                "POST",
                "/internal/face-tests",
                json={
                    "job_id": job_id,
                    "owner_id": user.id,
                    "filename": filename,
                    "detection_threshold": detection_threshold,
                    "min_face_size": min_face_size,
                },
            )
            succeeded = True
            return JSONResponse(response.json(), status_code=202)
        finally:
            # The worker moves accepted inputs into its private job directory.
            if not succeeded:
                path.unlink(missing_ok=True)


@router.get("/{job_id}")
def test_status(job_id: UUID, request: Request, user=Depends(current_user)):
    return request.app.state.worker.request(
        "GET", f"/internal/face-tests/{job_id}", params={"owner_id": user.id}
    ).json()


@router.get("/{job_id}/groups/{group_id}/image")
def image(job_id: UUID, group_id: int, request: Request, user=Depends(current_user)):
    response = request.app.state.worker.request(
        "GET",
        f"/internal/face-tests/{job_id}/groups/{group_id}/image",
        params={"owner_id": user.id},
    )
    return Response(response.content, media_type="image/jpeg")


@router.delete("")
async def delete_all(request: Request, user=Depends(current_user)):
    lock = request.app.state.face_test_upload_lock
    if lock.locked():
        raise HTTPException(409, "Wait for the video upload to finish")
    async with lock:
        response = await run_in_threadpool(
            request.app.state.worker.request,
            "DELETE",
            "/internal/face-tests",
            params={"owner_id": user.id},
            timeout=25,
        )
        return response.json()
