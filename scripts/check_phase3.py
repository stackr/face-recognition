"""Read-only native readiness report; no camera URLs, face images or vectors."""

import json
import time

from api_session import ROOT, api_session, checked


def main():
    with api_session() as client:
        deadline = time.monotonic() + 30
        while True:
            system = checked(client.get("/api/system/status"))
            worker = system.get("worker", {})
            if worker.get("status") == "ok":
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("Worker did not become ready")
            time.sleep(0.25)
        face = worker.get("face_analysis", {})
        if system.get("phase", 0) < 3 or face.get("embedding_dimension") != 512:
            raise RuntimeError("Phase 3 worker required")
        if face.get("status") != "passed" or worker["detector"]["actual_device"] != "cuda:0":
            raise RuntimeError("Requested native CUDA validation did not pass")
        if any(item.get("cuda_conv_count", 0) < 1 for item in face["models"].values()):
            raise RuntimeError("Actual CUDA convolutions required for every face model")
        cameras = []
        for camera in checked(client.get("/api/cameras")):
            state = checked(client.get(f"/api/cameras/{camera['camera_id']}/status"))
            cameras.append(
                {
                    key: camera[key]
                    for key in ("camera_id", "source_type", "enabled", "has_test_video")
                }
                | {"state": state["state"]}
            )
        report = {
            "checked_at": system["checked_at"],
            "status": "passed",
            "phase": system["phase"],
            "services": system["services"],
            "worker": {
                "status": worker["status"],
                "detector_device": worker["detector"]["actual_device"],
                "face_device": face["actual_device"],
                "face_devices": face["actual_device_per_model"],
                "embedding_dimension": face["embedding_dimension"],
                "model_version": face["model_version"],
            },
            "registered_cameras": cameras,
        }
        if any(item["status"] != "ok" for item in system["services"].values()):
            raise RuntimeError("Native data service unavailable")
        (ROOT / "data/reports/phase3-runtime.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
