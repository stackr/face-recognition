"""Test one registered source; output never includes its URL or credentials."""

import argparse
import json
import time
from datetime import UTC, datetime

from api_session import ROOT, api_session, checked


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("camera_id", type=int, nargs="?")
    parser.add_argument("--keep-running", action="store_true")
    parser.add_argument("--status-only", action="store_true")
    args = parser.parse_args()
    with api_session() as client:
        cameras = checked(client.get("/api/cameras"))
        if args.camera_id is None:
            print(
                json.dumps(
                    [
                        {
                            "camera_id": c["camera_id"],
                            "source_type": c["source_type"],
                            "enabled": c["enabled"],
                        }
                        for c in cameras
                    ]
                )
            )
            return
        camera_id = args.camera_id
        current = checked(client.get(f"/api/cameras/{camera_id}/status"))
        if args.status_only:
            print(
                json.dumps(
                    {
                        key: current.get(key)
                        for key in (
                            "camera_id",
                            "state",
                            "error_code",
                            "resolution",
                            "captured_frames",
                            "processed_frames",
                            "detection_fps",
                            "max_people",
                        )
                    },
                    indent=2,
                )
            )
            return
        if current["state"] in {"opening", "running", "draining", "stopping"}:
            raise RuntimeError("Camera already active; preserved")
        status = checked(client.post(f"/api/cameras/{camera_id}/start", json={}))
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                status = checked(client.get(f"/api/cameras/{camera_id}/status"))
                if status["state"] in {"running", "error", "ended"}:
                    break
                time.sleep(0.5)
            if status["state"] == "running":
                with client.stream("GET", f"/api/cameras/{camera_id}/preview") as preview:
                    if preview.status_code != 200 or b"\xff\xd8" not in next(preview.iter_bytes()):
                        raise RuntimeError("Registered camera preview failed")
            report = {
                key: status.get(key)
                for key in (
                    "camera_id",
                    "state",
                    "error_code",
                    "resolution",
                    "input_fps",
                    "processed_frames",
                    "max_people",
                )
            }
            report.update(
                {
                    "checked_at": datetime.now(UTC).isoformat(),
                    "preview_verified": status["state"] == "running",
                }
            )
            (ROOT / "data/reports/registered-camera.json").write_text(
                json.dumps(report, indent=2) + "\n"
            )
            print(json.dumps(report, indent=2))
        finally:
            if not args.keep_running or status["state"] not in {"running", "error"}:
                checked(client.post(f"/api/cameras/{camera_id}/stop"))


if __name__ == "__main__":
    main()
