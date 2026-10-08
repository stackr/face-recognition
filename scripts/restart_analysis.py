"""Restart native analysis services and resume only previously active cameras."""

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from scripts.api_session import api_session, checked  # noqa: E402


def main():
    with api_session() as client:
        active = []
        for camera in checked(client.get("/api/cameras")):
            state = checked(client.get(f"/api/cameras/{camera['camera_id']}/status"))
            if state["state"] in {"opening", "running", "reconnecting", "draining"}:
                active.append(
                    (
                        camera["camera_id"],
                        {
                            "source_type": state["source_type"],
                            "loop": state.get("loop", True),
                            "person_detection_enabled": state.get("person_detection_enabled", True),
                            "face_detection_enabled": state.get("face_detection_enabled", True),
                        },
                    )
                )
        subprocess.run(
            ["systemctl", "--user", "restart", "cctv-worker.service", "cctv-backend.service"],
            check=True,
        )
        from app.core.config import Settings
        from app.services.worker_client import WorkerClient

        worker = WorkerClient(Settings())
        try:
            for _ in range(60):
                try:
                    worker.request("GET", "/internal/status")
                    response = client.get("/api/health")
                    if response.status_code == 200:
                        break
                except Exception:
                    pass
                time.sleep(1)
            else:
                raise RuntimeError("Analysis service restart timed out")
        finally:
            worker.client.close()
        for camera_id, selection in active:
            checked(
                client.post(
                    f"/api/cameras/{camera_id}/start",
                    json=selection,
                )
            )
        print({"status": "passed", "resumed_cameras": [item[0] for item in active]})


if __name__ == "__main__":
    main()
