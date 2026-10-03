import asyncio
import hmac
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import current_user, operator_user
from app.core.security import COOKIE_NAME, token_hash
from app.db.session import get_db
from app.models import AuthSession, CameraPermission, Person, PersonPermission, User
from app.models.foundation import utc_now
from app.services.events import event_output

router = APIRouter(tags=["Match events"])


@router.get("/api/events")
def events(
    request: Request,
    after_id: int = Query(default=0, ge=0),
    after_change_id: int | None = Query(default=None, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    latest: bool = False,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if latest and (after_id or after_change_id is not None):
        raise HTTPException(422, "Latest and cursor cannot be combined")
    if after_id and after_change_id is not None:
        raise HTTPException(422, "Event and change cursors cannot be combined")
    return request.app.state.events.page(
        db, user, after_id=after_id, after_change_id=after_change_id, limit=limit, latest=latest
    )


@router.get("/api/events/{event_id}")
def event(
    event_id: int,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    return event_output(db, request.app.state.events.find(db, user, event_id), user)


def image(event_id, request, user, db, kind):
    store = request.app.state.events
    row = store.find(db, user, event_id)
    name = row.face_image_path if kind == "face" else row.frame_image_path
    if not name or row.image_expires_at <= utc_now():
        raise HTTPException(404, "Event image unavailable")
    try:
        content = store.path(name).read_bytes()
    except FileNotFoundError:
        raise HTTPException(404, "Event image unavailable") from None
    return Response(content, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/api/events/{event_id}/face")
def face(
    event_id: int,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    return image(event_id, request, user, db, "face")


@router.get("/api/events/{event_id}/frame")
def frame(
    event_id: int,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    return image(event_id, request, user, db, "frame")


@router.api_route("/api/events/{event_id}/clip", methods=["GET", "HEAD"])
def clip(
    event_id: int,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    store = request.app.state.events
    row = store.find(db, user, event_id)
    if (
        row.clip_state != "ready"
        or not row.video_clip_path
        or not row.clip_expires_at
        or row.clip_expires_at <= utc_now()
    ):
        raise HTTPException(404, "Event clip unavailable")
    path = store.clip_path(row.video_clip_path)
    if not path.is_file():
        raise HTTPException(404, "Event clip unavailable")
    return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "no-store"})


def review(event_id, request, user, status):
    row = request.app.state.events.review(event_id, user, status)
    request.app.state.event_broker.notify()
    return row


@router.post("/api/events/{event_id}/confirm")
def confirm(event_id: int, request: Request, user: User = Depends(operator_user)):
    return review(event_id, request, user, "confirmed")


@router.post("/api/events/{event_id}/reject")
def reject(event_id: int, request: Request, user: User = Depends(operator_user)):
    return review(event_id, request, user, "rejected")


class DeleteEvents(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_ids: list[Annotated[int, Field(strict=True, ge=1)]] = Field(min_length=1, max_length=100)


@router.post("/api/events/delete")
def delete_events(payload: DeleteEvents, request: Request, user: User = Depends(operator_user)):
    rows = request.app.state.events.delete(payload.event_ids, user)
    request.app.state.event_broker.notify()
    return {"items": rows}


@router.delete("/api/events/{event_id}")
def delete_event(event_id: int, request: Request, user: User = Depends(operator_user)):
    row = request.app.state.events.delete([event_id], user)[0]
    request.app.state.event_broker.notify()
    return row


class Notification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: int = Field(ge=1)


@router.post("/internal/events/notify", include_in_schema=False)
async def notify(payload: Notification, request: Request):
    # Wake only; the journal supplies IDs and data. No submitted biometric payload.
    if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(403, "Local service required")
    if not hmac.compare_digest(
        request.headers.get("X-Service-Token", ""),
        request.app.state.settings.service_token.get_secret_value(),
    ):
        raise HTTPException(401, "Service authentication required")
    request.app.state.event_broker.notify()
    return {"status": "accepted"}


def socket_data(app, token, event_id=None):
    with Session(app.state.engine) as db:
        session = db.get(AuthSession, token_hash(token)) if token and len(token) <= 256 else None
        user = db.get(User, session.user_id) if session and session.expires_at > utc_now() else None
        if not user or not user.enabled:
            raise HTTPException(401, "Session unavailable")
        # A fresh transaction on every delivery and heartbeat observes revocation.
        signature = (
            user.role,
            tuple(
                db.execute(
                    select(CameraPermission.camera_id, CameraPermission.can_operate)
                    .where(CameraPermission.user_id == user.id)
                    .order_by(CameraPermission.camera_id)
                )
            ),
            tuple(
                db.scalars(
                    select(PersonPermission.person_id)
                    .join(Person)
                    .where(PersonPermission.user_id == user.id, Person.deleting.is_(False))
                    .order_by(PersonPermission.person_id)
                )
            ),
        )
        payload = None
        if event_id is not None:
            try:
                payload = event_output(
                    db, app.state.events.find(db, user, event_id, include_deleted=True), user
                )
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
        return signature, payload


@router.websocket("/ws/events")
async def socket(websocket: WebSocket):
    app, settings = websocket.app, websocket.app.state.settings

    async def close(code):
        try:
            async with asyncio.timeout(1):
                await websocket.close(code=code)
        except (TimeoutError, RuntimeError, WebSocketDisconnect):
            pass

    if websocket.headers.get("origin") not in settings.allowed_origins:
        await close(1008)
        return
    token = websocket.cookies.get(COOKIE_NAME, "")
    subscriber = None
    tasks = []
    try:
        signature, _ = await asyncio.to_thread(socket_data, app, token)
        subscriber = app.state.event_broker.subscribe()
        await websocket.accept()
        async with asyncio.timeout(settings.event_ws_send_timeout_seconds):
            await websocket.send_json({"type": "ready"})
        receive = asyncio.create_task(websocket.receive())
        overflow = asyncio.create_task(subscriber.overflow.wait())
        queued = asyncio.create_task(subscriber.queue.get())
        tasks = [receive, overflow, queued]
        while True:
            done, _ = await asyncio.wait(tasks, timeout=2, return_when=asyncio.FIRST_COMPLETED)
            if overflow in done:
                await close(1013)
                return
            if receive in done:
                message = receive.result()
                if message["type"] == "websocket.disconnect":
                    return
                # Read-only channel; arbitrary incoming messages are rejected.
                await close(1008)
                return
            event_id = queued.result() if queued in done else None
            updated_signature, payload = await asyncio.to_thread(socket_data, app, token, event_id)
            async with asyncio.timeout(settings.event_ws_send_timeout_seconds):
                if updated_signature != signature:
                    await websocket.send_json({"type": "resync"})
                if payload:
                    await websocket.send_json(payload)
                elif not done:
                    await websocket.send_json({"type": "heartbeat"})
            signature = updated_signature
            if queued in done:
                tasks.remove(queued)
                queued = asyncio.create_task(subscriber.queue.get())
                tasks.append(queued)
    except HTTPException:
        await close(1008)
    except OverflowError:
        await close(1013)
    except TimeoutError:
        await close(1013)
    except WebSocketDisconnect:
        pass
    except Exception:
        await close(1011)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if subscriber:
            app.state.event_broker.clients.discard(subscriber)
