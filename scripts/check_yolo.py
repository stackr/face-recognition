"""Verify real person detections on CUDA without RTSP or user image input."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def main():
    import cv2
    from app.core.config import Settings
    from app.worker.detector import YoloPersonDetector

    detector = YoloPersonDetector(Settings())
    capture = cv2.VideoCapture(str(ROOT / "data/videos/people.mp4"))
    try:
        ok, frame = capture.read()
    finally:
        capture.release()
    if not ok:
        raise RuntimeError("Sample video unavailable")
    boxes = detector.detect(frame)
    if not len(boxes):
        raise RuntimeError("No person detected in the official test sample")
    report = {**detector.info, "person_boxes": len(boxes), "resources": detector.resources()}
    path = ROOT / "data/reports/yolo.json"
    path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
