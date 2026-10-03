"""Refresh a separate smoke manifest against current public fixtures; no accuracy labels."""

import hashlib
import json
from pathlib import Path

import cv2
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def main():
    source = ROOT / "data/calibration/phase3-smoke.json"
    dataset = json.loads(source.read_text())
    dataset["purpose"] = "pipeline_smoke"
    for item in dataset["sources"]:
        path = ROOT / item["media_path"]
        item["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        if item["duration_seconds"] is None:
            with Image.open(path) as image:
                item["width"], item["height"] = image.size
        else:
            cap = cv2.VideoCapture(str(path))
            item["width"], item["height"] = (
                int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            )
            item["duration_seconds"] = cap.get(cv2.CAP_PROP_FRAME_COUNT) / cap.get(cv2.CAP_PROP_FPS)
            cap.release()
            for appearance in dataset["appearances"]:
                if appearance["source_id"] == item["source_id"]:
                    appearance["end_seconds"] = min(
                        appearance["end_seconds"], item["duration_seconds"]
                    )
    target = ROOT / "data/calibration/phase10-smoke.json"
    target.write_text(json.dumps(dataset, indent=2) + "\n")
    print({"status": "prepared", "purpose": "pipeline_smoke", "accuracy_calibrated": False})


if __name__ == "__main__":
    main()
