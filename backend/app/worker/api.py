import hmac
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

import cv2
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import ConfigDict, Field, SecretStr

from app.core.config import Settings
from app.schemas.face_tests import DEFAULT_MIN_FACE_SIZE, FaceTestOptions
from app.schemas.recognition import DetectionSelection, face_interval
from app.worker.runtime import WorkerRuntime


class FaceTestInput(FaceTestOptions):
    job_id: UUID
    owner_id: int = Field(gt=0)
    filename: str = Field(min_length=1, max_length=200)


class StartInput(DetectionSelection):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    source: SecretStr = Field(max_length=2048)
    source_type: Literal["mp4", "rtsp"]
    loop: bool = True


def private_video(source, settings):
    directory = settings.video_dir.resolve()
    path = Path(source).resolve()
    if path.parent != directory or path.suffix != ".mp4" or not path.is_file():
        raise HTTPException(422, "Invalid private video")
    return path


def create_worker(
    settings=None,
    detector=None,
    *,
    face_analyzer=None,
    reidentifier=None,
    enable_faces=True,
    enable_gallery=True,
):
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        cv2.setNumThreads(settings.opencv_threads)
        from app.worker.detector import YoloPersonDetector

        try:
            active_detector = detector or YoloPersonDetector(settings)
            from app.worker.faces import FaceAnalyzer

            active_faces = (face_analyzer or FaceAnalyzer(settings)) if enable_faces else None
            from app.worker.gallery import ReferenceGallery

            gallery = ReferenceGallery(settings) if enable_gallery else None
            from app.worker.events import EventWriter

            events = EventWriter(settings) if enable_gallery else None
            revision = 0
            if events:
                from sqlalchemy.orm import Session

                from app.services.recognition import load_sampling

                with Session(events.engine) as db:
                    revision, values = load_sampling(db, settings)
                for name, value in values.model_dump().items():
                    setattr(settings, name, value)
                if active_faces:
                    active_faces.info.setdefault("quality", {}).update(
                        interval_seconds=face_interval(values),
                        max_rois_per_frame=values.face_rois_per_frame,
                    )
        except Exception as exc:
            logging.getLogger("cctv.worker").error(
                "Detector startup failed; type=%s", type(exc).__name__
            )
            raise
        app.state.runtime = WorkerRuntime(
            settings, active_detector, active_faces, gallery, events, revision
        )
        if settings.reid_enabled:
            try:
                from app.worker.reid import OSNetReIdentifier

                app.state.runtime.reidentifier = reidentifier or OSNetReIdentifier(settings)
                app.state.runtime.reid_status = app.state.runtime.reidentifier.info
            except Exception as exc:
                app.state.runtime.reid_status = {
                    "status": "unavailable",
                    "error_type": type(exc).__name__,
                    "error_code": "reid_model_not_ready",
                }
                logging.getLogger("cctv.worker").warning(
                    "Optional Re-ID startup unavailable; type=%s", type(exc).__name__
                )
        if events and settings.clip_enabled:
            from app.worker.clips import ClipManager

            app.state.runtime.clips = ClipManager(settings, events.store)
        if events:
            from app.worker.diagnostics import DiagnosticsWriter

            app.state.runtime.diagnostics = DiagnosticsWriter(
                settings, events.engine, app.state.runtime
            )
        from app.worker.face_tests import FaceTestManager

        app.state.runtime.face_tests = FaceTestManager(settings, app.state.runtime)
        logging.getLogger("cctv.worker").info(
            "Worker ready; device=%s model=%s",
            active_detector.info["actual_device"],
            active_detector.info["model"],
        )
        yield
        app.state.runtime.close()

    app = FastAPI(
        title="Private analysis worker",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    def authorized(request: Request):
        value = request.headers.get("X-Service-Token", "")
        if not hmac.compare_digest(value, settings.service_token.get_secret_value()):
            raise HTTPException(401, "Service authentication required")

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return JSONResponse({"detail": "Invalid worker input"}, status_code=422)

    @app.get("/internal/status", dependencies=[Depends(authorized)])
    def health(request: Request):
        runtime = request.app.state.runtime
        with runtime.lock:
            states = [run.status() for run in runtime.runs.values()]
        return {
            "status": "ok",
            "detector": runtime.detector.info,
            "face_analysis": runtime.face_analyzer.info
            if runtime.face_analyzer
            else {"status": "disabled"},
            "person_reid": runtime.reid_status,
            "resources": runtime.detector.resources(),
            "cameras": states,
            "events": runtime.events.status() if runtime.events else {"status": "disabled"},
            "recognition_logs": runtime.diagnostics.status()
            if runtime.diagnostics
            else {"status": "disabled"},
            "sampling": runtime.sampling_status(),
            "scheduler": runtime.scheduler_status(),
            "clips": runtime.clips.status() if runtime.clips else {"status": "disabled"},
        }

    @app.get("/internal/face-tests", dependencies=[Depends(authorized)])
    def face_test_list(request: Request, owner_id: int = Query(gt=0)):
        manager = request.app.state.runtime.face_tests
        items = manager.list(owner_id)
        return {
            "items": items,
            "defaults": {
                "detection_threshold": settings.face_detection_threshold,
                "min_face_size": DEFAULT_MIN_FACE_SIZE,
                "match_threshold": manager.runtime.settings.face_match_threshold,
            },
            "can_start": (
                manager.active is None
                and len(items) < settings.face_test_max_jobs_per_user
                and len(manager.jobs) < 100
                and manager.runtime.face_analyzer is not None
                and not manager.cancel.is_set()
            ),
            "limits": {
                "upload_max_mb": settings.video_upload_max_mb,
                "retention_hours": settings.face_test_retention_hours,
                "max_duration_seconds": settings.face_test_max_duration_seconds,
            },
        }

    @app.post("/internal/face-tests", dependencies=[Depends(authorized)])
    def face_test_start(payload: FaceTestInput, request: Request):
        try:
            return request.app.state.runtime.face_tests.start(
                str(payload.job_id),
                payload.owner_id,
                payload.filename,
                detection_threshold=payload.detection_threshold,
                min_face_size=payload.min_face_size,
                match_threshold=payload.match_threshold,
            )
        except OverflowError:
            raise HTTPException(429, "Video test limit reached") from None
        except ValueError:
            raise HTTPException(422, "Invalid video test") from None
        except RuntimeError:
            raise HTTPException(503, "Face analysis unavailable") from None

    @app.delete("/internal/face-tests", dependencies=[Depends(authorized)])
    def face_test_delete(request: Request, owner_id: int = Query(gt=0)):
        try:
            return request.app.state.runtime.face_tests.delete_all(owner_id)
        except TimeoutError:
            raise HTTPException(503, "Video test deletion pending") from None

    @app.get("/internal/face-tests/{job_id}", dependencies=[Depends(authorized)])
    def face_test_status(job_id: UUID, request: Request, owner_id: int = Query(gt=0)):
        try:
            return request.app.state.runtime.face_tests.get(str(job_id), owner_id)
        except KeyError:
            raise HTTPException(404, "Video test unavailable") from None

    @app.get(
        "/internal/face-tests/{job_id}/groups/{group_id}/image", dependencies=[Depends(authorized)]
    )
    def face_test_image(job_id: UUID, group_id: int, request: Request, owner_id: int = Query(gt=0)):
        try:
            content = request.app.state.runtime.face_tests.image(str(job_id), group_id, owner_id)
        except KeyError:
            raise HTTPException(404, "Video test image unavailable") from None
        return Response(content, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/internal/settings", dependencies=[Depends(authorized)])
    def sampling(request: Request):
        return request.app.state.runtime.sampling_status()

    @app.post("/internal/settings/reload", dependencies=[Depends(authorized)])
    def reload_sampling(request: Request):
        runtime = request.app.state.runtime
        if runtime.diagnostics is None:
            raise HTTPException(503, "Persistent settings unavailable")
        revision = runtime.diagnostics.reload()
        deadline = time.monotonic() + 8
        while runtime.sampling_status()["revision"] < revision:
            if time.monotonic() >= deadline or runtime.cancel.is_set():
                raise HTTPException(503, "Settings application pending")
            time.sleep(0.01)
        return runtime.sampling_status()

    @app.post("/internal/videos/probe", dependencies=[Depends(authorized)])
    def probe(payload: StartInput):
        if payload.source_type != "mp4":
            raise HTTPException(422, "MP4 required")
        path = private_video(payload.source.get_secret_value(), settings)
        with path.open("rb") as stream:
            header = stream.read(12)
        if header[4:8] != b"ftyp":
            raise HTTPException(422, "MP4 container required")
        cap = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG)
        try:
            if (
                not cap.isOpened()
                or not 0 < cap.get(cv2.CAP_PROP_FRAME_WIDTH) <= 3840
                or not 0 < cap.get(cv2.CAP_PROP_FRAME_HEIGHT) <= 2160
            ):
                raise HTTPException(422, "Unreadable or oversized MP4")
            ok, frame = cap.read()
            if not ok or frame.shape[0] > 2160 or frame.shape[1] > 3840:
                raise HTTPException(422, "Unreadable or oversized MP4")
            return {
                "resolution": [frame.shape[1], frame.shape[0]],
                "fps": cap.get(cv2.CAP_PROP_FPS),
            }
        finally:
            cap.release()

    @app.post("/internal/references/analyze", dependencies=[Depends(authorized)])
    async def reference(request: Request):
        from starlette.concurrency import run_in_threadpool

        from app.worker.reference_images import ReferenceRejected

        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > settings.reference_upload_max_mb * 1024 * 1024:
                raise HTTPException(413, "Reference image too large")
        try:
            return await run_in_threadpool(
                request.app.state.runtime.analyze_reference, bytes(content)
            )
        except ReferenceRejected as exc:
            return JSONResponse(
                {"detail": {"code": exc.code, "quality": exc.quality}}, status_code=422
            )
        except OverflowError:
            raise HTTPException(429, "Reference queue full") from None
        except (RuntimeError, TimeoutError):
            raise HTTPException(503, "Reference analysis unavailable") from None

    @app.post("/internal/gallery/reload", dependencies=[Depends(authorized)])
    def reload_gallery(payload: dict, request: Request):
        gallery = request.app.state.runtime.gallery
        if gallery is None:
            raise HTTPException(503, "Gallery unavailable")
        return gallery.reload(int(payload.get("revision", 0)))

    @app.post("/internal/references/search", dependencies=[Depends(authorized)])
    def search_reference(payload: dict, request: Request):
        gallery = request.app.state.runtime.gallery
        if gallery is None:
            raise HTTPException(503, "Gallery unavailable")
        if payload.get("provider") not in {None, "auto", "memory", "qdrant"}:
            raise HTTPException(422, "Invalid search provider")
        return gallery.search(
            payload["embedding"],
            allowed_person_ids=payload["allowed_person_ids"],
            provider=payload.get("provider"),
            limit=10,
        )

    @app.post("/internal/cameras/{camera_id}/start", dependencies=[Depends(authorized)])
    def start(camera_id: int, payload: StartInput, request: Request):
        source = payload.source.get_secret_value()
        if camera_id < 1:
            raise HTTPException(422, "Invalid camera ID")
        if payload.source_type == "mp4":
            source = str(private_video(source, settings))
        else:
            try:
                parsed = urlsplit(source)
                valid = parsed.scheme in {"rtsp", "rtsps"} and bool(parsed.hostname)
                _ = parsed.port
            except ValueError:
                valid = False
            if not valid:
                raise HTTPException(422, "Invalid RTSP source")
        person_enabled = payload.person_detection_enabled
        face_enabled = payload.face_detection_enabled
        if payload.source_type == "rtsp":
            if person_enabled is False:
                raise HTTPException(422, "RTSP requires person detection")
            person_enabled, face_enabled = True, False
        elif person_enabled is None and not settings.person_detection_enabled and not face_enabled:
            raise HTTPException(422, "Select at least one detector")
        try:
            return request.app.state.runtime.start(
                camera_id,
                source,
                payload.source_type,
                payload.loop,
                person_detection_enabled=person_enabled,
                face_detection_enabled=face_enabled,
            )
        except ValueError:
            raise HTTPException(409, "Camera already active") from None
        except OverflowError:
            raise HTTPException(429, "Active camera limit reached") from None

    def camera(request, camera_id):
        try:
            return request.app.state.runtime.get(camera_id)
        except KeyError:
            raise HTTPException(404, "Camera not started") from None

    @app.get("/internal/cameras/{camera_id}", dependencies=[Depends(authorized)])
    def status(camera_id: int, request: Request):
        return camera(request, camera_id).status()

    @app.post("/internal/cameras/{camera_id}/stop", dependencies=[Depends(authorized)])
    def stop(camera_id: int, request: Request):
        camera(request, camera_id)
        try:
            return request.app.state.runtime.stop(camera_id)
        except TimeoutError:
            raise HTTPException(503, "Capture still stopping") from None

    @app.get("/internal/cameras/{camera_id}/frame", dependencies=[Depends(authorized)])
    def frame(camera_id: int, request: Request):
        run = camera(request, camera_id)
        with run.lock:
            if run.state not in {"running", "draining"} or run.jpeg is None or run.result is None:
                return Response(
                    status_code=204,
                    headers={
                        "X-Stream-Session": run.stream_session_id,
                        "X-Camera-State": run.state,
                    },
                )
            result = run.result
            return Response(
                run.jpeg,
                media_type="image/jpeg",
                headers={
                    "X-Stream-Session": result["stream_session_id"],
                    "X-Camera-State": run.state,
                    "X-Frame-Id": str(result["frame_id"]),
                    "X-Captured-At": result["captured_at"],
                    "Cache-Control": "no-store",
                },
            )

    @app.get("/internal/cameras/{camera_id}/people/{track_id}", dependencies=[Depends(authorized)])
    def person_thumbnail(camera_id: int, track_id: int, stream_session_id: str, request: Request):
        run = camera(request, camera_id)
        with run.lock:
            cached = run.person_images.get(track_id)
            if (
                stream_session_id != run.stream_session_id
                or run.state not in {"running", "draining"}
                or not run.person_detection_enabled
                or cached is None
                or time.monotonic() - cached[2] > settings.track_lost_seconds
            ):
                raise HTTPException(404, "Person image not available")
            return Response(
                cached[0],
                media_type="image/jpeg",
                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
            )

    @app.get("/internal/cameras/{camera_id}/faces/{track_id}", dependencies=[Depends(authorized)])
    def thumbnail(camera_id: int, track_id: int, stream_session_id: str, request: Request):
        run = camera(request, camera_id)
        with run.lock:
            if (
                stream_session_id != run.stream_session_id
                or run.state not in {"running", "draining"}
                or run.faces is None
            ):
                raise HTTPException(404, "Face not available")
            jpeg = run.faces.thumbnail(track_id, stream_session_id, time.monotonic())
            if jpeg is None:
                raise HTTPException(404, "Face not available")
            return Response(
                jpeg,
                media_type="image/jpeg",
                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
            )

    return app
