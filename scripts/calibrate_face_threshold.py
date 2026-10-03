"""Offline private evaluation; never updates gallery, events or saved thresholds."""

import argparse
import hashlib
import json
import math
import os
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.schemas.calibration import CalibrationDataset  # noqa: E402
from app.services.calibration import PairScore, open_set_metrics, pair_report  # noqa: E402


def source_paths(dataset):
    paths = {}
    for source in dataset.sources:
        path = (ROOT / source.media_path).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file():
            raise ValueError("Media must exist inside the private project directory")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != source.sha256:
            raise ValueError("Ground truth media checksum mismatch")
        paths[source.source_id] = path
    return paths


def feedback():
    from api_session import api_session, checked

    counts = {"confirmed": 0, "rejected": 0, "candidate": 0}
    with api_session() as client:
        cursor = 0
        for _ in range(500):
            page = checked(client.get("/api/events", params={"after_id": cursor, "limit": 100}))
            for event in page["items"]:
                if event["status"] in counts:
                    counts[event["status"]] += 1
            if not page["has_more"]:
                break
            cursor = page["next_cursor"]
        else:
            raise RuntimeError("Feedback pagination limit exceeded")
    reviewed = counts["confirmed"] + counts["rejected"]
    return {
        "status": "unavailable",
        "purpose": "candidate_feedback",
        "counts": counts,
        "candidate_confirmation_fraction": counts["confirmed"] / reviewed if reviewed else None,
        "recall": None,
        "FPR": None,
        "FNR": None,
        "roc": None,
        "reason": "Candidate-only feedback omits missed positives and non-candidate negatives",
    }


def native_pairs(dataset, paths, analyzer):
    import cv2
    import numpy as np

    references, quality = {}, {}
    sources = {s.source_id: s for s in dataset.sources}
    for reference in dataset.references:
        source = sources[reference.source_id]
        if source.duration_seconds is not None:
            raise ValueError("Pair references must be images; use annotated appearances for MP4")
        image = cv2.imread(str(paths[source.source_id]))
        if image is None or image.shape[:2] != (source.height, source.width):
            raise ValueError("Ground truth image dimensions mismatch")
        x1, y1, x2, y2 = map(int, reference.crop)
        candidate = analyzer.inspect_reference(image[y1:y2, x1:x2])
        quality[reference.reference_id] = {
            key: candidate.metadata.get(key) for key in ("status", "quality", "reasons")
        }
        if candidate.metadata["status"] == "accepted":
            references[reference.reference_id] = analyzer.embed(candidate.aligned)
    rows = []
    by_id = {r.reference_id: r for r in dataset.references}
    for trial in dataset.trials:
        a, b = references.get(trial.first), references.get(trial.second)
        score = float(np.clip(a @ b, -1, 1)) if a is not None and b is not None else None
        rows.append(
            PairScore(
                trial.first,
                trial.second,
                trial.same_subject,
                sources[by_id[trial.second].source_id].split,
                score,
                "quality_rejected" if score is None else None,
            )
        )
    return rows, references, quality


class EvaluationGallery:
    def __init__(self, settings, embeddings, references):
        self.settings = settings
        self.references = references
        self.embeddings = embeddings

    def snapshot(self):
        return (1, tuple(self.embeddings)), self.references

    def search_snapshot(self, vector, snapshot, *, limit):
        best = {}
        for reference in self.references:
            if reference.reference_id not in self.embeddings:
                continue
            score = float(max(-1, min(1, vector @ self.embeddings[reference.reference_id])))
            current = best.get(reference.subject_id)
            if current is None or current["similarity"] < score:
                best[reference.subject_id] = {
                    "person_id": reference.subject_id,
                    "face_id": reference.reference_id,
                    "person_name": reference.subject_id,
                    "similarity": score,
                }
        return {"matches": sorted(best.values(), key=lambda x: -x["similarity"])[:limit]}


def pipeline(dataset, paths, analyzer, embeddings, gallery_ids, settings, thresholds):
    import cv2
    from app.worker.detector import YoloPersonDetector
    from app.worker.faces import TrackFaces
    from app.worker.runtime import Frame
    from app.worker.tracker import CameraTracker

    refs = [r for r in dataset.references if r.reference_id in gallery_ids]
    members = {r.subject_id for r in refs}
    if any(reference_id not in embeddings for reference_id in gallery_ids):
        return {
            "status": "unavailable",
            "reason": "Enrollment reference rejected by production quality checks",
        }
    if (
        not refs
        or not dataset.appearances
        or any(a.face_region is None for a in dataset.appearances)
    ):
        return {
            "status": "unavailable",
            "reason": "Explicit gallery and manually annotated appearance face regions are required",
        }
    if any(a.gallery_member != (a.subject_id in members) for a in dataset.appearances):
        raise ValueError("Gallery membership conflicts with human appearance labels")
    detector = YoloPersonDetector(settings)
    sources = {s.source_id: s for s in dataset.sources}
    evaluated = {t: [] for t in thresholds}
    unassigned = {t: set() for t in thresholds}
    for source_id in sorted({a.source_id for a in dataset.appearances}):
        source = sources[source_id]
        cap = cv2.VideoCapture(
            str(paths[source_id]),
            cv2.CAP_FFMPEG,
            [cv2.CAP_PROP_N_THREADS, settings.capture_decode_threads],
        )
        fps = cap.get(cv2.CAP_PROP_FPS)
        duration = cap.get(cv2.CAP_PROP_FRAME_COUNT) / fps if fps > 0 else 0
        if (
            not cap.isOpened()
            or not math.isfinite(fps)
            or fps <= 0
            or abs(duration - source.duration_seconds) > max(0.1, 1 / fps)
        ):
            cap.release()
            raise ValueError("Ground truth video duration mismatch")
        appearances = [a for a in dataset.appearances if a.source_id == source_id]
        gallery = EvaluationGallery(
            settings, {k: v for k, v in embeddings.items() if k in gallery_ids}, refs
        )
        tracker = CameraTracker(settings)
        caches = {
            t: TrackFaces(settings.model_copy(update={"face_match_threshold": t}))
            for t in thresholds
        }
        observations = {
            t: [
                {
                    "subject_id": a.subject_id,
                    "gallery_member": a.gallery_member,
                    "scores": {},
                    "split": source.split,
                    "matched": set(),
                }
                for a in appearances
            ]
            for t in thresholds
        }
        # Share actual detector/face inference across thresholds, while retaining the
        # production sample window, weights and two-sample criterion per threshold.
        base = TrackFaces(settings)
        frame_index, next_due = 0, 0.0
        try:
            while True:
                ok, image = cap.read()
                if not ok:
                    break
                second = frame_index / fps
                frame_index += 1
                if second + 1e-8 < next_due:
                    continue
                next_due = second + 1 / settings.detection_fps
                if image.shape[:2] != (source.height, source.width):
                    raise ValueError("Ground truth video dimensions mismatch")
                frame = Frame(
                    image,
                    "a" * 32,
                    frame_index,
                    datetime.fromtimestamp(second, UTC).isoformat(),
                    second,
                )
                tracks = tracker.update(detector.detect(image), image, second)
                base.process(analyzer, frame, tracks, tracker.live_ids())
                for threshold, cache in caches.items():
                    # Pure comparison state is independent; embeddings remain private.
                    import copy

                    cache.tracks = copy.deepcopy(base.tracks)
                    cache.match(gallery, tracks)
                    for track in tracks:
                        face = track.get("face", {})
                        bbox = face.get("bbox")
                        if bbox is None:
                            continue
                        cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
                        associated = [
                            i
                            for i, a in enumerate(appearances)
                            if a.start_seconds <= second < a.end_seconds
                            and a.face_region[0] <= cx <= a.face_region[2]
                            and a.face_region[1] <= cy <= a.face_region[3]
                        ]
                        if len(associated) != 1:
                            unassigned[threshold].update(
                                (source_id, track["track_id"], m["person_id"])
                                for m in face.get("matches", [])
                            )
                            continue
                        observation = observations[threshold][associated[0]]
                        observation["matched"].update(
                            m["person_id"] for m in face.get("matches", [])
                        )
            for threshold, rows in observations.items():
                for row in rows:
                    row["scores"] = {person: [1.0] for person in row.pop("matched")}
                evaluated[threshold].extend(rows)
        finally:
            cap.release()
    split_reports = {}
    for split in ("smoke", "calibration", "evaluation"):
        tables = []
        for threshold in thresholds:
            rows = [q for q in evaluated[threshold] if q["split"] == split]
            if not rows:
                continue
            # Evaluate any saved identity candidate per appearance. Wrong identities
            # count as false positives even alongside a correctly detected identity.
            known = sum(q["gallery_member"] for q in rows)
            unknown = len(rows) - known
            correct = sum(q["gallery_member"] and q["subject_id"] in q["scores"] for q in rows)
            false_unknown = sum(not q["gallery_member"] and bool(q["scores"]) for q in rows)
            wrong = sum(
                any(person != q["subject_id"] for person in q["scores"])
                for q in rows
                if q["gallery_member"]
            )
            tables.append(
                {
                    "threshold": threshold,
                    "TP": correct,
                    "FP": false_unknown + wrong,
                    "FN": known - correct,
                    "TN": unknown - false_unknown,
                    "precision": correct / (correct + false_unknown + wrong)
                    if correct + false_unknown + wrong
                    else None,
                    "recall": correct / known if known else None,
                    "FPR": false_unknown / unknown if unknown else None,
                    "FNR": (known - correct) / known if known else None,
                    "unknown_false_identification_rate": false_unknown / unknown
                    if unknown
                    else None,
                    "registered_miss_rate": (known - correct) / known if known else None,
                    "registered_wrong_identity_rate": wrong / known if known else None,
                }
            )
        split_reports[split] = {
            "status": ("smoke_only" if dataset.purpose == "pipeline_smoke" else "available")
            if tables
            else "unavailable",
            "thresholds": tables,
        }
    return {
        "status": "available" if dataset.purpose == "threshold_evaluation" else "smoke_only",
        "unit": "manually annotated appearance interval; any production consensus candidate",
        "splits": split_reports,
        "detector": detector.info,
        "unassigned_candidate_tracks": {str(t): len(values) for t, values in unassigned.items()},
        "scope": "annotated appearances only; candidates outside or ambiguous in annotations are reported separately",
        "whole_scene_precision": None,
        "whole_scene_precision_reason": "Full-scene annotation completeness is not declared by this schema",
        "association": "unique face center inside human region and interval; ambiguous associations ignored",
        "roc": None,
        "roc_reason": "Appearance results are threshold-dependent production consensus; face-pair ROC reported separately",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--feedback-only", action="store_true")
    parser.add_argument("--pipeline", action="store_true")
    parser.add_argument("--gallery-reference", nargs="+", default=[])
    parser.add_argument(
        "--thresholds", type=float, nargs="+", default=[0.65, 0.70, 0.75, 0.80, 0.85]
    )
    parser.add_argument("--output", type=Path, default=ROOT / "data/reports/face-calibration.json")
    args = parser.parse_args()
    if not args.thresholds or any(
        not math.isfinite(t) or not -1 <= t <= 1 for t in args.thresholds
    ):
        parser.error("Thresholds must be finite cosine values in [-1,1]")
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "accuracy_calibrated": False,
        "threshold_applied": False,
    }
    if args.feedback_only:
        report.update(feedback())
    elif args.dataset is None:
        report.update(
            status="unavailable", reason="Independent human ground truth dataset required"
        )
    else:
        import cv2
        from app.core.config import Settings
        from app.worker.faces import FaceAnalyzer

        dataset = CalibrationDataset.model_validate_json(args.dataset.read_text())
        if len(set(args.gallery_reference)) != len(args.gallery_reference) or not set(
            args.gallery_reference
        ) <= {r.reference_id for r in dataset.references}:
            parser.error("Gallery reference IDs must be unique and present in the dataset")
        sources = {s.source_id: s for s in dataset.sources}
        refs = {r.reference_id: r for r in dataset.references}
        if dataset.purpose == "threshold_evaluation" and any(
            sources[refs[r].source_id].split == "evaluation" for r in args.gallery_reference
        ):
            parser.error("Evaluation probes must not be enrolled in the gallery")
        paths = source_paths(dataset)
        settings = Settings()
        from api_session import api_session, checked

        with api_session() as client:
            active = checked(client.get("/api/system/status"))["worker"].get("cameras", [])
            if any(
                r["state"] in {"opening", "running", "reconnecting", "draining", "stopping"}
                for r in active
            ):
                raise RuntimeError("Native calibration requires an idle worker")
            controls = checked(client.get("/api/function-settings"))["values"]
        for key, value in controls.items():
            setattr(settings, key, value)
        cv2.setNumThreads(settings.opencv_threads)
        analyzer = FaceAnalyzer(settings)
        scores, embeddings, quality = native_pairs(dataset, paths, analyzer)
        queries = []
        gallery = [refs[r] for r in args.gallery_reference]
        members = {r.subject_id for r in gallery}
        for reference in dataset.references:
            if reference.reference_id in args.gallery_reference:
                continue
            scores_by_person = {}
            vector = embeddings.get(reference.reference_id)
            for enrolled in gallery:
                other = embeddings.get(enrolled.reference_id)
                if vector is None or other is None:
                    continue
                score = float(max(-1, min(1, vector @ other)))
                scores_by_person[enrolled.subject_id] = [
                    max(score, scores_by_person.get(enrolled.subject_id, [-1])[0])
                ]
            queries.append(
                {
                    "subject_id": reference.subject_id,
                    "gallery_member": reference.subject_id in members,
                    "scores": scores_by_person,
                    "split": sources[reference.source_id].split,
                }
            )
        pairs = pair_report(scores, args.thresholds, dataset.purpose)
        report.update(
            status="passed",
            purpose=dataset.purpose,
            provenance=dataset.provenance,
            dataset_sha256=hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
            face_model=analyzer.info,
            gallery_rejected_reference_ids=[
                r for r in args.gallery_reference if r not in embeddings
            ],
            quality_settings={
                k: getattr(settings, k)
                for k in [
                    "face_min_size",
                    "face_min_blur",
                    "face_quality_threshold",
                    "face_max_yaw",
                    "face_max_pitch",
                    "face_max_roll",
                ]
            },
            sampling=controls,
            gallery_reference_ids=args.gallery_reference,
            gallery_aggregation="maximum reference cosine per person; production temporal matching uses quality-weighted mean and >=2 supporting samples",
            quality_results=quality,
            pair_scores=[asdict(r) for r in scores],
            pairs=pairs,
            open_set={
                split: {
                    "status": ("smoke_only" if dataset.purpose == "pipeline_smoke" else "available")
                    if gallery
                    and all(r.reference_id in embeddings for r in gallery)
                    and any(q["split"] == split for q in queries)
                    else "unavailable",
                    "thresholds": [
                        open_set_metrics([q for q in queries if q["split"] == split], t)
                        for t in args.thresholds
                    ]
                    if gallery and all(r.reference_id in embeddings for r in gallery)
                    else [],
                }
                for split in ["smoke", "calibration", "evaluation"]
            },
            pipeline=pipeline(
                dataset,
                paths,
                analyzer,
                embeddings,
                args.gallery_reference,
                settings,
                args.thresholds,
            )
            if args.pipeline
            else {
                "status": "unavailable",
                "reason": "Run with --pipeline and annotated MP4 ground truth",
            },
            accuracy_status="smoke_only"
            if dataset.purpose == "pipeline_smoke"
            else pairs["status"],
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    os.chmod(args.output, 0o600)
    print(
        json.dumps(
            {
                k: report.get(k)
                for k in [
                    "status",
                    "purpose",
                    "accuracy_status",
                    "accuracy_calibrated",
                    "threshold_applied",
                    "reason",
                ]
            }
        )
    )


if __name__ == "__main__":
    main()
