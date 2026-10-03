"""Independent ground-truth scoring; never derive missed positives from candidate events."""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class PairScore:
    first: str
    second: str
    same_subject: bool
    split: str
    score: float | None
    rejection: str | None = None

    def __post_init__(self):
        if self.score is not None and (not math.isfinite(self.score) or not -1 <= self.score <= 1):
            raise ValueError("Cosine score must be finite and in [-1,1]")


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def confusion(rows, threshold, *, include_rejections=False):
    counts = dict(TP=0, FP=0, FN=0, TN=0)
    evaluated = 0
    for row in rows:
        if row.score is None and not include_rejections:
            continue
        accepted = row.score is not None and row.score >= threshold
        key = ("TP" if accepted else "FN") if row.same_subject else ("FP" if accepted else "TN")
        counts[key] += 1
        evaluated += 1
    tp, fp, fn, tn = (counts[k] for k in ("TP", "FP", "FN", "TN"))
    return counts | {
        "evaluated": evaluated,
        "quality_rejected": sum(r.score is None for r in rows),
        "precision": ratio(tp, tp + fp),
        "recall": ratio(tp, tp + fn),
        "FPR": ratio(fp, fp + tn),
        "FNR": ratio(fn, tp + fn),
        "F1": ratio(2 * tp, 2 * tp + fp + fn),
    }


def roc(rows):
    scored = [r for r in rows if r.score is not None]
    if not any(r.same_subject for r in scored) or not any(not r.same_subject for r in scored):
        return {
            "status": "unavailable",
            "reason": "Both positive and negative scored trials required",
            "auc": None,
            "points": [],
        }
    thresholds = [1.000001, *sorted({r.score for r in scored}, reverse=True), -1.000001]
    points = []
    for threshold in thresholds:
        metrics = confusion(scored, threshold)
        points.append({"threshold": threshold, "FPR": metrics["FPR"], "TPR": metrics["recall"]})
    auc = sum(
        (b["FPR"] - a["FPR"]) * (a["TPR"] + b["TPR"]) / 2
        for a, b in zip(points, points[1:], strict=False)
    )
    return {
        "status": "available",
        "auc": auc,
        "points": points,
        "unit": "quality-accepted face pairs",
    }


def pair_report(rows, thresholds, purpose):
    splits = {}
    for split in ("smoke", "calibration", "evaluation"):
        selected = [r for r in rows if r.split == split]
        if not selected:
            splits[split] = {"status": "unavailable", "reason": "No trials in this split"}
            continue
        splits[split] = {
            "status": "available" if purpose == "threshold_evaluation" else "smoke_only",
            "trials": len(selected),
            "face_pairs": [dict(threshold=t, **confusion(selected, t)) for t in thresholds],
            "attempts_including_quality_rejections": [
                dict(threshold=t, **confusion(selected, t, include_rejections=True))
                for t in thresholds
            ],
            "roc": roc(selected),
        }
    calibration = [r for r in rows if r.split == "calibration" and r.score is not None]
    evaluation = [r for r in rows if r.split == "evaluation" and r.score is not None]
    independent = purpose == "threshold_evaluation" and all(
        any(r.same_subject for r in group) and any(not r.same_subject for r in group)
        for group in (calibration, evaluation)
    )
    proposal = None
    if independent:
        proposal = max(thresholds, key=lambda t: (confusion(calibration, t)["F1"] or 0, t))
    return {
        "unit": "face_pair; first=enrollment anchor, second=probe",
        "splits": splits,
        "threshold_proposal": proposal,
        "selection_objective": "maximum calibration F1; ties prefer higher threshold",
        "fixed_threshold_evaluation": confusion(evaluation, proposal)
        if proposal is not None
        else None,
        "status": "available" if independent else "unavailable",
        "reason": None
        if independent
        else "Independent calibration and evaluation positives/negatives are required",
        "threshold_applied": False,
    }


def open_set_metrics(queries, threshold, minimum_samples=1):
    """One query/appearance: per-person max-reference cosine, then consensus."""
    known = unknown = correct = false_unknown = missed = misidentified = 0
    for query in queries:
        expected = query["subject_id"] if query["gallery_member"] else None
        eligible = {
            person: sum(scores) / len(scores)
            for person, scores in query.get("scores", {}).items()
            if len(scores) >= minimum_samples
            and sum(s >= threshold for s in scores) >= minimum_samples
        }
        predicted = max(eligible, key=eligible.get) if eligible else None
        if predicted is not None and eligible[predicted] < threshold:
            predicted = None
        if expected is None:
            unknown += 1
            false_unknown += predicted is not None
        else:
            known += 1
            correct += predicted == expected
            missed += predicted != expected
            misidentified += predicted is not None and predicted != expected
        # A missing/rejected face stays in the denominator via its empty scores.
    tp, fp, fn, tn = correct, false_unknown + misidentified, missed, unknown - false_unknown
    return {
        "threshold": threshold,
        "known_queries": known,
        "unknown_queries": unknown,
        "TP": tp,
        "FP": fp,
        "FN": fn,
        "TN": tn,
        "precision": ratio(tp, tp + fp),
        "recall": ratio(tp, known),
        "FPR": ratio(false_unknown, unknown),
        "FNR": ratio(missed, known),
        "unknown_false_identification_rate": ratio(false_unknown, unknown),
        "registered_miss_rate": ratio(missed, known),
        "registered_wrong_identity_rate": ratio(misidentified, known),
    }
