import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.dependencies import current_user
from app.api.persons import filter_camera_status
from app.db.session import get_db
from app.models import User

router = APIRouter(tags=["System"])


@router.get("/api/health")
def health():
    return {"status": "ok", "phase": 11}


@router.get("/api/system/status")
def system_status(
    request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    dependencies = {}
    try:
        db.execute(text("SELECT 1"))
        dependencies["mariadb"] = {"status": "ok"}
    except Exception:
        db.rollback()
        dependencies["mariadb"] = {"status": "unavailable"}
    try:
        request.app.state.qdrant.get_collections()
        dependencies["qdrant"] = {"status": "ok"}
    except Exception:
        dependencies["qdrant"] = {"status": "unavailable"}
    worker = {"status": "unavailable"}
    try:
        worker = request.app.state.worker.request("GET", "/internal/status").json()
        if user.role != "admin":
            worker.pop("cameras", None)
            worker.pop("events", None)
        else:
            worker["cameras"] = [
                filter_camera_status(db, user, state) for state in worker.get("cameras", [])
            ]
    except Exception:
        worker = {"status": "unavailable"}
    settings = request.app.state.settings
    gpu = {
        "status": "unverified",
        "requested_device": settings.requested_device,
        "allow_cpu_fallback": settings.allow_cpu_fallback,
    }
    path = settings.gpu_report_path
    if not path.is_absolute():
        from app.core.config import ROOT

        path = ROOT / path
    if path.exists():
        try:
            report = json.loads(path.read_text())
            gpu.update(
                {
                    key: report[key]
                    for key in (
                        "status",
                        "checked_at",
                        "actual_device",
                        "gpu_name",
                        "torch_version",
                        "torch_cuda",
                        "cudnn_version",
                        "onnxruntime_version",
                        "cuda_node_count",
                    )
                    if key in report
                }
            )
        except (OSError, ValueError):
            gpu["status"] = "invalid_report"
    return {
        "phase": 11,
        "checked_at": datetime.now(UTC).isoformat(),
        "services": dependencies,
        "gpu": gpu,
        "worker": worker,
    }
