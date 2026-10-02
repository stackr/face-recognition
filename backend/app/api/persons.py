from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.dependencies import admin_user, current_user, operator_user
from app.core.face_data import MODEL_VERSION
from app.db.session import get_db
from app.models import FaceCleanupJob, GalleryState, Person, PersonFace, PersonPermission, User
from app.models.foundation import utc_now
from app.services.references import allowed_person_ids, audit, bump, face_public

router = APIRouter(prefix="/api/persons", tags=["Target persons"])


class PersonInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def name_required(cls, value):
        if not value.strip():
            raise ValueError("Person name required")
        return value.strip()


class AccessInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=64)
    can_view: bool = True


def find(db, person_id, user):
    person = db.get(Person, person_id)
    if person is None or person.deleting:
        raise HTTPException(404, "Person unavailable")
    if user.role != "admin" and db.get(PersonPermission, (person_id, user.id)) is None:
        raise HTTPException(403, "Person permission required")
    return person


def output(db, person, settings):
    faces = list(
        db.scalars(
            select(PersonFace)
            .where(PersonFace.person_id == person.id, PersonFace.state != "deleting")
            .order_by(PersonFace.id)
        )
    )
    pending = db.scalar(
        select(FaceCleanupJob.id).where(
            FaceCleanupJob.person_id == person.id, FaceCleanupJob.status == "pending"
        )
    )
    return {
        "id": person.id,
        "name": person.name,
        "description": person.description,
        "enabled": person.enabled,
        "sync_status": "pending" if pending else "ready",
        "faces": [face_public(face, settings) for face in faces],
        "created_at": person.created_at.isoformat() + "Z",
        "updated_at": person.updated_at.isoformat() + "Z",
    }


def filter_matches(db, user, result):
    """Fence worker results against committed SQL state and current person grants."""
    db.rollback()
    allowed = set(allowed_person_ids(db, user))
    revision = db.get(GalleryState, 1).revision
    if result.get("gallery_revision") != revision:
        return []
    eligible = set(
        db.scalars(
            select(PersonFace.id)
            .join(Person)
            .where(
                Person.id.in_(allowed),
                Person.enabled.is_(True),
                Person.deleting.is_(False),
                PersonFace.state == "ready",
                PersonFace.embedding_expires_at > utc_now(),
                PersonFace.embedding_encrypted.is_not(None),
                PersonFace.model_version == MODEL_VERSION,
            )
        )
    )
    return [
        item
        for item in result.get("matches", [])
        if item["person_id"] in allowed and item["face_id"] in eligible
    ]


def filter_camera_status(db, user, state):
    result = state.get("result")
    if not result:
        return state
    tracks = result.get("tracks", [])
    flattened = [match for track in tracks for match in track.get("face", {}).get("matches", [])]
    matches = filter_matches(
        db, user, {"gallery_revision": result.get("gallery_revision"), "matches": flattened}
    )
    eligible = {(match["person_id"], match["face_id"]) for match in matches}
    for track in tracks:
        face = track.get("face", {})
        face["matches"] = [
            match
            for match in face.get("matches", [])
            if (match["person_id"], match["face_id"]) in eligible
        ]
    if result.get("gallery_revision") != db.get(GalleryState, 1).revision:
        result["search_status"] = "syncing"
    return state


async def analyze(request):
    if request.headers.get("content-type", "").split(";")[0] not in {"image/jpeg", "image/png"}:
        raise HTTPException(415, "Use a JPEG or PNG image")
    content = bytearray()
    limit = request.app.state.settings.reference_upload_max_mb * 1024**2
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > limit:
            raise HTTPException(413, "Reference image too large")
    worker = request.app.state.worker
    reply = await run_in_threadpool(
        worker.request,
        "POST",
        "/internal/references/analyze",
        content=bytes(content),
        headers={"Content-Type": "application/octet-stream"},
    )
    return reply.json()


@router.get("")
def list_persons(request: Request, user=Depends(current_user), db=Depends(get_db)):
    rows = db.scalars(
        select(Person).where(Person.id.in_(allowed_person_ids(db, user))).order_by(Person.id)
    )
    return [output(db, person, request.app.state.settings) for person in rows]


@router.post("", status_code=201)
def create(payload: PersonInput, request: Request, user=Depends(admin_user), db=Depends(get_db)):
    with request.app.state.references.lock:
        db.rollback()
        count = db.scalar(select(func.count()).select_from(Person))
        if count >= request.app.state.settings.max_target_persons:
            raise HTTPException(409, "Target person limit reached")
        person = Person(**payload.model_dump())
        db.add(person)
        db.flush()
        audit(db, user.id, "person.create", person.id)
        bump(db)
        request.app.state.references.enqueue(db, person.id)
        db.commit()
        synchronized = request.app.state.references.reconcile(person.id)
        db.expire_all()
        return JSONResponse(
            output(db, person, request.app.state.settings), status_code=201 if synchronized else 202
        )


@router.post("/search")
async def search(request: Request, user=Depends(operator_user), db=Depends(get_db)):
    if request.app.state.reference_upload_lock.locked():
        raise HTTPException(429, "Another reference request is in progress")
    async with request.app.state.reference_upload_lock:
        analyzed = await analyze(request)
        db.rollback()
        ids = allowed_person_ids(db, user)
        result = (
            await run_in_threadpool(
                request.app.state.worker.request,
                "POST",
                "/internal/references/search",
                json={
                    "embedding": analyzed["embedding"],
                    "allowed_person_ids": ids,
                },
            )
        ).json()
        result["matches"] = filter_matches(db, user, result)
        for match in result["matches"]:
            match["candidate"] = match["similarity"] >= result["threshold"]
        result["quality"] = analyzed["quality"]
        audit(db, user.id, "person.search", "gallery")
        db.commit()
        return result


@router.post("/maintenance/retry")
def retry(request: Request, user=Depends(admin_user)):
    service = request.app.state.references
    with service.lock, Session(service.engine) as db:
        jobs = list(db.scalars(select(FaceCleanupJob).where(FaceCleanupJob.status == "pending")))
        for job in jobs:
            job.next_attempt_at = utc_now()
        audit(db, user.id, "person.maintenance.retry", "gallery")
        db.commit()
    service.cleanup()
    with Session(service.engine) as db:
        return {
            "pending": db.scalar(
                select(func.count())
                .select_from(FaceCleanupJob)
                .where(FaceCleanupJob.status == "pending")
            )
        }


@router.get("/{person_id}")
def get(person_id: int, request: Request, user=Depends(current_user), db=Depends(get_db)):
    return output(db, find(db, person_id, user), request.app.state.settings)


@router.put("/{person_id}")
def update(
    person_id: int,
    payload: PersonInput,
    request: Request,
    user=Depends(admin_user),
    db=Depends(get_db),
):
    service = request.app.state.references
    with service.lock:
        db.rollback()
        person = find(db, person_id, user)
        for key, value in payload.model_dump().items():
            setattr(person, key, value)
        bump(db)
        service.enqueue(db, person_id)
        audit(db, user.id, "person.update", person_id)
        db.commit()
        synchronized = service.reconcile(person_id)
        db.expire_all()
        return JSONResponse(
            output(db, person, request.app.state.settings), status_code=200 if synchronized else 202
        )


@router.delete("/{person_id}")
def remove(person_id: int, request: Request, user=Depends(admin_user), db=Depends(get_db)):
    service = request.app.state.references
    with service.lock:
        db.rollback()
        person = find(db, person_id, user)
        person.deleting, person.enabled = True, False
        for face in db.scalars(select(PersonFace).where(PersonFace.person_id == person_id)):
            face.state = "deleting"
        bump(db)
        service.enqueue(db, person_id)
        audit(db, user.id, "person.delete", person_id)
        db.commit()
        synchronized = service.reconcile(person_id)
        return (
            Response(status_code=204)
            if synchronized
            else JSONResponse({"sync_status": "pending"}, status_code=202)
        )


@router.put("/{person_id}/access")
def set_access(
    person_id: int,
    payload: AccessInput,
    request: Request,
    user=Depends(admin_user),
    db=Depends(get_db),
):
    with request.app.state.references.lock:
        db.rollback()
        find(db, person_id, user)
        target = db.scalar(
            select(User).where(User.username == payload.username, User.enabled.is_(True))
        )
        if target is None:
            raise HTTPException(404, "Account not found")
        grant = db.get(PersonPermission, (person_id, target.id))
        if payload.can_view and grant is None:
            db.add(PersonPermission(person_id=person_id, user_id=target.id))
        elif not payload.can_view and grant:
            db.delete(grant)
        audit(db, user.id, "person.access.update", person_id)
        db.commit()
        return payload.model_dump()


@router.post("/{person_id}/faces")
async def upload(person_id: int, request: Request, user=Depends(admin_user), db=Depends(get_db)):
    find(db, person_id, user)
    user_id = user.id
    if request.app.state.reference_upload_lock.locked():
        raise HTTPException(429, "Another reference request is in progress")
    async with request.app.state.reference_upload_lock:
        analyzed = await analyze(request)
        face, synchronized = await run_in_threadpool(
            request.app.state.references.upload, person_id, analyzed, user_id
        )
        return JSONResponse(face, status_code=201 if synchronized else 202)


@router.get("/{person_id}/faces/{face_id}/image")
def image(
    person_id: int, face_id: int, request: Request, user=Depends(current_user), db=Depends(get_db)
):
    with request.app.state.references.lock:
        db.rollback()
        find(db, person_id, user)
        face = db.get(PersonFace, face_id)
        if (
            face is None
            or face.person_id != person_id
            or face.state == "deleting"
            or not face.image_path
            or face.image_expires_at <= utc_now()
        ):
            raise HTTPException(404, "Reference image unavailable")
        path = request.app.state.references.path(face.image_path)
        if not path.is_file():
            raise HTTPException(404, "Reference image unavailable")
        content = path.read_bytes()
        audit(db, user.id, "person.image.read", person_id)
        db.commit()
        return Response(content, media_type="image/jpeg")


@router.delete("/{person_id}/faces/{face_id}")
def remove_face(
    person_id: int, face_id: int, request: Request, user=Depends(admin_user), db=Depends(get_db)
):
    service = request.app.state.references
    with service.lock:
        db.rollback()
        find(db, person_id, user)
        face = db.get(PersonFace, face_id)
        if face is None or face.person_id != person_id or face.state == "deleting":
            raise HTTPException(404, "Reference unavailable")
        face.state = "deleting"
        bump(db)
        service.enqueue(db, person_id)
        audit(db, user.id, "person.face.delete", person_id)
        db.commit()
        synchronized = service.reconcile(person_id)
        return (
            Response(status_code=204)
            if synchronized
            else JSONResponse({"sync_status": "pending"}, status_code=202)
        )
