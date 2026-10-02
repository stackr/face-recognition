"""Private, synthetic smoke video and manually labelled ground truth examples."""

import json
import os
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def main():
    from app.schemas.calibration import CalibrationDataset
    from app.worker.face_onnx import sha256

    original = ROOT / "data/samples/zidane.jpg"
    image = cv2.imread(str(original))
    if image is None:
        raise RuntimeError("Run prepare_phase3.py first")
    directory = ROOT / "data/calibration"
    directory.mkdir(parents=True, exist_ok=True)
    video = ROOT / "data/videos/face-smoke.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 10, (1280, 720))
    if not writer.isOpened():
        raise RuntimeError("MP4 writer unavailable")
    try:
        # A repeated still image is deliberately marked smoke-only, not CCTV evidence.
        for _ in range(400):
            writer.write(image)
    finally:
        writer.release()
    os.chmod(video, 0o600)
    sources = []
    for identifier, path, duration in (("photo", original, None), ("video", video, 40)):
        sources.append(
            {
                "source_id": identifier,
                "source_group": "public-zidane-photo",
                "split": "smoke",
                "media_path": str(path.relative_to(ROOT)),
                "sha256": sha256(path),
                "width": 1280,
                "height": 720,
                "duration_seconds": duration,
            }
        )
    # Regions were marked by visual inspection, independently of detector output.
    regions = {"person_a": [475, 200, 680, 460], "person_b": [895, 65, 1075, 305]}
    references = []
    for subject, bbox in regions.items():
        x1, y1, x2, y2 = bbox
        crop = image[y1:y2, x1:x2]
        for suffix, pixels in (
            ("reference", crop),
            ("variant", cv2.convertScaleAbs(crop, alpha=0.98, beta=3)),
        ):
            identifier = subject + "_" + suffix
            path = directory / (identifier + ".jpg")
            if not cv2.imwrite(str(path), pixels):
                raise RuntimeError("Sample encoding failed")
            os.chmod(path, 0o600)
            sources.append(
                {
                    "source_id": identifier,
                    "source_group": "public-zidane-photo",
                    "split": "smoke",
                    "media_path": str(path.relative_to(ROOT)),
                    "sha256": sha256(path),
                    "width": pixels.shape[1],
                    "height": pixels.shape[0],
                }
            )
            references.append(
                {
                    "reference_id": identifier,
                    "source_id": identifier,
                    "subject_id": subject,
                    "crop": [0, 0, pixels.shape[1], pixels.shape[0]],
                }
            )
    dataset = CalibrationDataset.model_validate(
        {
            "schema_version": 1,
            "purpose": "pipeline_smoke",
            "provenance": "Public Ultralytics zidane.jpg; two anonymous subjects manually labelled by left/right location. Repeated still video and brightness variants are dependent samples. No consented CCTV calibration/evaluation dataset has been collected.",
            "sources": sources,
            "appearances": [
                {
                    "source_id": "video",
                    "subject_id": subject,
                    "start_seconds": 0,
                    "end_seconds": 40,
                    "face_region": region,
                    "gallery_member": subject == "person_b",
                    "annotated_by": "manual_visual_inspection",
                }
                for subject, region in regions.items()
            ],
            "references": references,
            "trials": [
                {"first": "person_b_reference", "second": "person_b_variant", "same_subject": True},
                {
                    "first": "person_a_reference",
                    "second": "person_b_reference",
                    "same_subject": False,
                },
            ],
        }
    )
    (directory / "phase3-smoke.json").write_text(dataset.model_dump_json(indent=2) + "\n")
    (directory / "schema.json").write_text(
        json.dumps(CalibrationDataset.model_json_schema(), indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                "status": "passed",
                "video": str(video.relative_to(ROOT)),
                "duration_seconds": 40,
                "resolution": [1280, 720],
                "input_fps": 10,
                "sources": len(sources),
                "pairs": len(dataset.trials),
                "purpose": dataset.purpose,
                "far_frr": "unavailable",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
