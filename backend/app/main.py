import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from qdrant_client import QdrantClient
from sqlalchemy.exc import SQLAlchemyError

from app.api import analysis, auth, cameras, events, persons, recognition, system
from app.core.config import Settings
from app.core.logging import configure_logging
from app.core.security import LoginLimiter
from app.db.session import make_engine
from app.services.camera_operations import CameraOperations
from app.services.event_stream import EventBroker
from app.services.events import EventStore
from app.services.recognition import RecognitionStore
from app.services.references import ReferenceService
from app.services.worker_client import ViewerLimits, WorkerClient


def create_app(
    settings: Settings | None = None,
    engine=None,
    vector_client=None,
    worker_transport=None,
    *,
    start_cleanup=True,
) -> FastAPI:
    settings = settings or Settings()
    engine = engine if engine is not None else make_engine(settings)
    vector_client = (
        vector_client
        if vector_client is not None
        else QdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key.get_secret_value() or None,
            timeout=2,
            check_compatibility=False,
        )
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging(settings.log_dir)
        logging.getLogger("cctv").info("API started; phase=6")
        await app.state.event_broker.start()

        async def maintain_events():
            while True:
                try:
                    await asyncio.to_thread(app.state.events.cleanup)
                    await asyncio.to_thread(app.state.recognition.cleanup)
                except Exception as exc:
                    logging.getLogger("cctv.events").warning(
                        "Event maintenance failed; type=%s", type(exc).__name__
                    )
                await asyncio.sleep(settings.event_cleanup_interval_seconds)

        maintenance = asyncio.create_task(maintain_events()) if start_cleanup else None
        if start_cleanup:
            app.state.references.start()
        yield
        if maintenance:
            maintenance.cancel()
            await asyncio.gather(maintenance, return_exceptions=True)
        await app.state.event_broker.close()
        app.state.references.close()
        vector_client.close()
        app.state.worker.client.close()
        engine.dispose()

    app = FastAPI(title="CCTV Search", version="0.6.0", lifespan=lifespan)
    app.state.settings, app.state.engine = settings, engine
    app.state.qdrant, app.state.login_limiter = vector_client, LoginLimiter()
    app.state.worker = WorkerClient(settings, worker_transport)
    app.state.viewers = ViewerLimits(settings)
    app.state.upload_lock = asyncio.Lock()
    app.state.reference_upload_lock = asyncio.Lock()
    app.state.camera_operations = CameraOperations()
    app.state.references = ReferenceService(settings, engine, vector_client, app.state.worker)
    app.state.events = EventStore(settings, engine)
    app.state.recognition = RecognitionStore(settings, engine)
    app.state.event_broker = EventBroker(app.state.events)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin is not None and origin not in settings.allowed_origins:
                return JSONResponse({"detail": "Origin not allowed"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        # Pydantic's default error response includes submitted input, including secrets.
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {"loc": list(error["loc"]), "type": error["type"], "msg": "Invalid value"}
                    for error in exc.errors()
                ]
            },
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError):
        logging.getLogger("cctv").error(
            "Database request failed; error_type=%s", type(exc).__name__
        )
        return JSONResponse({"detail": "Database temporarily unavailable"}, status_code=503)

    app.include_router(auth.router)
    app.include_router(cameras.router)
    app.include_router(analysis.router)
    app.include_router(persons.router)
    app.include_router(system.router)
    app.include_router(events.router)
    app.include_router(recognition.router)
    return app


app = create_app()
