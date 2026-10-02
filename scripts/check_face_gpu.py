"""Real public face input, quality gate, aligned ArcFace and actual CUDA nodes."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def main():
    from app.core.config import Settings
    from app.worker.face_onnx import FaceModels
    from app.worker.faces import FaceAnalyzer, quality

    settings = Settings()
    models = FaceModels(settings)
    analyzer = FaceAnalyzer(settings, models)
    image = cv2.imread(str(ROOT / "data/samples/zidane.jpg"))
    if image is None:
        raise RuntimeError("Run prepare_phase3.py first")
    detected = models.detect(image)
    candidates = []
    for face in detected:
        candidate = quality(image, face, settings, models.pose(image, face["bbox"]))
        record = candidate.metadata
        if candidate.aligned is not None:
            vector = analyzer.embed(candidate.aligned)
            assert vector.shape == (512,) and np.isfinite(vector).all()
            assert abs(float(np.linalg.norm(vector)) - 1) < 1e-5
            record |= {
                "embedding_dimension": len(vector),
                "embedding_norm": float(np.linalg.norm(vector)),
            }
        candidates.append(record)
    if not any(candidate.get("embedding_dimension") == 512 for candidate in candidates):
        raise RuntimeError("No qualified real face embedding")
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "status": models.info["status"],
        "models": analyzer.info,
        "input": "public-zidane-sample",
        "faces": candidates,
        "cuda_proof": "executed convolution nodes per model during startup; real-image inference also completed",
        "quality_calibrated": False,
    }
    destination = ROOT / "data/reports/face-gpu.json"
    destination.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if settings.requested_device == "cuda" and models.info["status"] != "passed":
        raise SystemExit("Requested CUDA face verification did not pass")


if __name__ == "__main__":
    main()
