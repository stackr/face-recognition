import hmac
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import cv2
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.core.config import Settings
from app.worker.runtime import WorkerRuntime


class StartInput(BaseModel):
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


def create_worker(settings=None, detector=None):
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        from app.worker.detector import YoloPersonDetector

        try:
            active_detector = detector or YoloPersonDetector(settings)
        except Exception as exc:
            logging.getLogger("cctv.worker").error(
                "Detector startup failed; type=%s", type(exc).__name__
            )
            raise
        app.state.runtime = WorkerRuntime(settings, active_detector)
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
            "resources": runtime.detector.resources(),
            "cameras": states,
        }

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
        try:
            return request.app.state.runtime.start(
                camera_id, source, payload.source_type, payload.loop
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
            if run.jpeg is None or run.result is None:
                return Response(status_code=204)
            result = run.result
            return Response(
                run.jpeg,
                media_type="image/jpeg",
                headers={
                    "X-Stream-Session": result["stream_session_id"],
                    "X-Frame-Id": str(result["frame_id"]),
                    "X-Captured-At": result["captured_at"],
                    "Cache-Control": "no-store",
                },
            )

    return app
