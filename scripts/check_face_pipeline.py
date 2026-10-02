"""Regression: repeated shared YOLO/ONNX inference on a separate worker thread."""

import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def main():
    from app.core.config import Settings
    from app.worker.detector import YoloPersonDetector
    from app.worker.faces import FaceAnalyzer, TrackFaces
    from app.worker.runtime import Frame
    from app.worker.tracker import CameraTracker

    settings = Settings()
    detector = YoloPersonDetector(settings)
    analyzer = FaceAnalyzer(settings)
    tracker = CameraTracker(settings)
    faces = TrackFaces(settings)

    def infer():
        capture = cv2.VideoCapture(str(ROOT / "data/videos/face-smoke.mp4"))
        ok, frame = capture.read()
        capture.release()
        assert ok
        totals = Counter()
        for index in range(80):
            now = 10 + index * 0.6
            tracks = tracker.update(detector.detect(frame), frame, now)
            counts = faces.process(
                analyzer,
                Frame(frame, "a" * 32, index + 1, "2026-10-02T00:00:00Z", now),
                tracks,
                tracker.live_ids(),
            )
            totals.update(counts)
        if totals["embeddings_created"] < 1 or totals["roi_attempts"] < 100:
            raise RuntimeError("Expected qualified real faces in repeated GPU pipeline")
        return dict(totals)

    with ThreadPoolExecutor(max_workers=1) as executor:
        totals = executor.submit(infer).result()
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "status": "passed"
        if detector.info["actual_device"].startswith("cuda")
        and analyzer.info["actual_device"] == "cuda"
        else "cpu"
        if settings.requested_device == "cpu"
        else "cpu_fallback",
        "mode": "fixed_public_frame_repeat",
        "iterations": 80,
        "detector_device": detector.info["actual_device"],
        "face_device": analyzer.info["actual_device"],
        "face_counts": totals,
        "latency_benchmark": False,
    }
    (ROOT / "data/reports/face-pipeline.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if settings.requested_device == "cuda" and report["status"] != "passed":
        raise SystemExit("Requested CUDA pipeline verification did not pass")


if __name__ == "__main__":
    main()
