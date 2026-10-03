"""Real CUDA OSNet smoke plus isolated worker integration; no gallery/DB writes."""

import json
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.core.config import Settings  # noqa: E402
from app.worker.api import create_worker  # noqa: E402
from app.worker.reid import OSNetReIdentifier  # noqa: E402


def main():
    settings = Settings(reid_enabled=True, clip_enabled=False)
    image = cv2.imread(str(ROOT / "data/samples/zidane.jpg"))
    if image is None:
        raise RuntimeError("Public sample missing")
    model = OSNetReIdentifier(settings)
    first = image[150:720, 160:760]
    second = image[30:710, 745:1250]
    a = model.extract_embedding(first)
    variant = model.extract_embedding(
        np.clip(first.astype(np.float32) * 1.05, 0, 255).astype(np.uint8)
    )
    b = model.extract_embedding(second)
    if (
        model.info["actual_device"] != "cuda:0"
        or a.shape != (512,)
        or not np.isclose(np.linalg.norm(a), 1, atol=1e-5)
    ):
        raise RuntimeError("Native CUDA body embedding required")
    same = model.compare(a, a)
    positive, negative = model.compare(a, variant), model.compare(a, b)
    if not same > 0.999 or not positive > negative:
        raise RuntimeError("Body appearance smoke comparison failed")
    headers = {"X-Service-Token": settings.service_token.get_secret_value()}
    with TestClient(create_worker(settings, reidentifier=model, enable_gallery=False)) as client:
        started = client.post(
            "/internal/cameras/9001/start",
            headers=headers,
            json={
                "source": str(ROOT / "data/videos/face-smoke.mp4"),
                "source_type": "mp4",
                "loop": True,
            },
        )
        if started.status_code != 200:
            raise RuntimeError("Isolated native worker start failed")
        deadline = time.monotonic() + 20
        status = {}
        try:
            while time.monotonic() < deadline:
                status = client.get("/internal/cameras/9001", headers=headers).json()
                if status.get("reid_counts", {}).get("embeddings_created", 0) >= 2:
                    break
                if status.get("state") == "error":
                    raise RuntimeError("Isolated worker inference failed")
                time.sleep(0.1)
            else:
                raise RuntimeError(
                    f"Body track embedding timeout: {status.get('reid_counts', {})}; model={client.get('/internal/status', headers=headers).json().get('person_reid')}"
                )
            ready = [
                t["reid"]
                for t in status["result"]["tracks"]
                if t.get("reid", {}).get("embedding_ready")
            ]
            if len(ready) < 2 or any("embedding" in t or "vector" in t for t in ready):
                raise RuntimeError("Body metadata privacy check failed")
            worker_info = client.get("/internal/status", headers=headers).json()
            device = worker_info["person_reid"]["actual_device"]
            if (
                worker_info["detector"]["actual_device"] != "cuda:0"
                or worker_info["face_analysis"]["actual_device"] != "cuda"
            ):
                raise RuntimeError("Native detector/face CUDA required")
            stopped = client.post("/internal/cameras/9001/stop", headers=headers).json()
            if stopped["reid_cache_tracks"] != 0:
                raise RuntimeError("Stopped body cache not released")
        finally:
            client.post("/internal/cameras/9001/stop", headers=headers)
    report = {
        "status": "passed",
        "purpose": "pipeline_smoke",
        "accuracy_calibrated": False,
        "model": model.info,
        "self_similarity": same,
        "brightness_variant_similarity": positive,
        "different_body_similarity": negative,
        "worker_actual_device": device,
        "embeddings_created": status["reid_counts"]["embeddings_created"],
        "checks": [
            "pinned_weights",
            "cuda_embedding",
            "normalized_512",
            "body_compare",
            "native_worker_tracks",
            "metadata_only",
            "stop_erases_cache",
        ],
    }
    output = ROOT / "data/reports/phase11-native.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    os.chmod(output, 0o600)
    print(
        json.dumps(
            {
                k: report[k]
                for k in [
                    "status",
                    "purpose",
                    "accuracy_calibrated",
                    "worker_actual_device",
                    "checks",
                ]
            }
        )
    )


if __name__ == "__main__":
    main()
