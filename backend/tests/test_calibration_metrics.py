import copy

import pytest
from app.schemas.calibration import CalibrationDataset
from app.services.calibration import PairScore, confusion, open_set_metrics, pair_report, roc


def pair(label, score, split="calibration"):
    return PairScore("anchor", "probe", label, split, score)


def test_counts_threshold_boundary_and_quality_missing_denominators():
    rows = [
        pair(True, 0.8),
        pair(True, 0.5),
        pair(False, 0.75),
        pair(False, 0.1),
        pair(True, None),
        pair(False, None),
    ]
    raw = confusion(rows, 0.75)
    assert [raw[k] for k in ["TP", "FP", "FN", "TN"]] == [1, 1, 1, 1]
    assert raw["precision"] == raw["recall"] == raw["FPR"] == raw["FNR"] == 0.5
    full = confusion(rows, 0.75, include_rejections=True)
    assert [full[k] for k in ["TP", "FP", "FN", "TN"]] == [1, 1, 2, 2]
    assert full["recall"] == pytest.approx(1 / 3)


def test_roc_perfect_reverse_ties_and_single_class_unavailable():
    assert roc([pair(True, 0.9), pair(False, 0.1)])["auc"] == 1
    assert roc([pair(True, 0.1), pair(False, 0.9)])["auc"] == 0
    assert roc([pair(True, 0.5), pair(False, 0.5)])["auc"] == 0.5
    assert roc([pair(True, 0.9)])["status"] == "unavailable"
    assert confusion([], 0.75)["recall"] is None


def test_tuning_uses_calibration_only_and_keeps_fixed_evaluation():
    rows = [
        pair(True, 0.8),
        pair(False, 0.7),
        pair(True, 0.5, "evaluation"),
        pair(False, 0.9, "evaluation"),
    ]
    report = pair_report(rows, [0.65, 0.75, 0.85], "threshold_evaluation")
    assert report["threshold_proposal"] == 0.75
    assert (
        report["fixed_threshold_evaluation"]["FN"]
        == report["fixed_threshold_evaluation"]["FP"]
        == 1
    )
    assert report["threshold_applied"] is False
    assert pair_report(rows, [0.65, 0.75, 0.85], "pipeline_smoke")["threshold_proposal"] is None


def test_open_set_unknown_false_id_known_miss_and_wrong_id():
    queries = [
        {"subject_id": "A", "gallery_member": True, "scores": {"A": [0.9]}},
        {"subject_id": "A", "gallery_member": True, "scores": {}},
        {"subject_id": "A", "gallery_member": True, "scores": {"B": [0.9]}},
        {"subject_id": "U", "gallery_member": False, "scores": {"A": [0.8]}},
        {"subject_id": "U", "gallery_member": False, "scores": {}},
    ]
    result = open_set_metrics(queries, 0.75)
    assert [result[k] for k in ["TP", "FP", "FN", "TN"]] == [1, 2, 2, 1]
    assert result["unknown_false_identification_rate"] == 0.5
    assert result["registered_miss_rate"] == pytest.approx(2 / 3)
    assert open_set_metrics(queries, 0.95)["registered_miss_rate"] == 1


@pytest.mark.parametrize("score", [float("nan"), float("inf"), 1.01, -1.01])
def test_nonfinite_or_out_of_cosine_range_rejected(score):
    with pytest.raises(ValueError):
        pair(True, score)


def test_human_dataset_rejects_capture_group_leak_and_anchor_probe_reuse():
    sources = [
        {
            "source_id": str(i),
            "source_group": f"capture-{i}",
            "split": split,
            "media_path": f"data/{i}.jpg",
            "sha256": "a" * 64,
            "width": 160,
            "height": 160,
        }
        for i, split in enumerate(["calibration", "evaluation"])
    ]
    data = {
        "purpose": "threshold_evaluation",
        "provenance": "manual independent captures",
        "sources": sources,
        "appearances": [],
        "references": [
            {
                "reference_id": str(i),
                "source_id": str(i),
                "subject_id": "A",
                "crop": [0, 0, 160, 160],
            }
            for i in range(2)
        ],
        "trials": [{"first": "0", "second": "1", "same_subject": True}],
    }
    assert CalibrationDataset.model_validate(data)
    leaked = copy.deepcopy(data)
    leaked["sources"][1]["source_group"] = "capture-0"
    with pytest.raises(ValueError, match="Capture group leaked"):
        CalibrationDataset.model_validate(leaked)
    same = copy.deepcopy(data)
    same["sources"][1]["split"] = "calibration"
    same["sources"][1]["source_group"] = "capture-0"
    with pytest.raises(ValueError, match="independent captures"):
        CalibrationDataset.model_validate(same)


def test_source_integrity_and_private_project_boundary(tmp_path, monkeypatch):
    import hashlib
    import importlib.util
    from pathlib import Path

    path = Path(__file__).parents[2] / "scripts/calibrate_face_threshold.py"
    spec = importlib.util.spec_from_file_location("calibration_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    data = tmp_path / "face.jpg"
    data.write_bytes(b"private-test-image")
    source = {
        "source_id": "image",
        "source_group": "independent-capture",
        "split": "smoke",
        "media_path": "face.jpg",
        "sha256": hashlib.sha256(data.read_bytes()).hexdigest(),
        "width": 160,
        "height": 160,
    }
    dataset = CalibrationDataset.model_validate(
        {
            "purpose": "pipeline_smoke",
            "provenance": "unit-test",
            "sources": [source],
            "references": [],
            "appearances": [],
            "trials": [],
        }
    )
    assert module.source_paths(dataset)["image"] == data
    data.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="checksum mismatch"):
        module.source_paths(dataset)


def test_video_pipeline_keeps_undetected_known_and_unknown_in_denominators(
    tmp_path, monkeypatch, app_context
):
    import importlib.util
    from pathlib import Path
    from types import SimpleNamespace

    import app.worker.detector as detectors
    import numpy as np
    from test_worker import boxes, write_video

    path = Path(__file__).parents[2] / "scripts/calibrate_face_threshold.py"
    spec = importlib.util.spec_from_file_location("video_calibration_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(
        detectors,
        "YoloPersonDetector",
        lambda _: SimpleNamespace(
            info={"actual_device": "cpu"}, detect=lambda image: boxes(empty=True)
        ),
    )
    video = tmp_path / "video.mp4"
    write_video(video, seconds=1)
    dataset = CalibrationDataset.model_validate(
        {
            "purpose": "pipeline_smoke",
            "provenance": "unit test manual intervals",
            "sources": [
                {
                    "source_id": "video",
                    "source_group": "g",
                    "split": "smoke",
                    "media_path": "data/video.mp4",
                    "sha256": "a" * 64,
                    "width": 160,
                    "height": 120,
                    "duration_seconds": 1,
                },
                {
                    "source_id": "photo",
                    "source_group": "g",
                    "split": "smoke",
                    "media_path": "data/photo.jpg",
                    "sha256": "b" * 64,
                    "width": 160,
                    "height": 120,
                },
            ],
            "references": [
                {
                    "reference_id": "anchor",
                    "source_id": "photo",
                    "subject_id": "A",
                    "crop": [0, 0, 160, 120],
                }
            ],
            "trials": [],
            "appearances": [
                {
                    "source_id": "video",
                    "subject_id": label,
                    "start_seconds": 0,
                    "end_seconds": 1,
                    "face_region": [0, 0, 80, 100],
                    "gallery_member": registered,
                    "annotated_by": "human",
                }
                for label, registered in [("A", True), ("U", False)]
            ],
        }
    )
    result = module.pipeline(
        dataset,
        {"video": video},
        None,
        {"anchor": np.ones(512, dtype=np.float32)},
        ["anchor"],
        app_context[2],
        [0.7],
    )
    counts = result["splits"]["smoke"]["thresholds"][0]
    assert counts["TP"] == 0 and counts["FN"] == 1 and counts["TN"] == 1
    assert counts["registered_miss_rate"] == 1 and counts["unknown_false_identification_rate"] == 0
