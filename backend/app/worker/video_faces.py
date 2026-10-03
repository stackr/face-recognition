"""All-frame face extraction and ephemeral, per-video appearance clustering."""

import math
from dataclasses import dataclass, field, replace

import cv2
import numpy as np

from app.core.face_data import normalized_embedding
from app.schemas.face_tests import DEFAULT_MIN_FACE_SIZE
from app.worker.face_onnx import align_face


class VideoTestError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def overlap(a, b):
    intersection = np.maximum(0, np.minimum(a[2:], b[2:]) - np.maximum(a[:2], b[:2]))
    area = float(np.prod(intersection))
    return area / max(float(np.prod(a[2:] - a[:2]) + np.prod(b[2:] - b[:2]) - area), 1e-8)


def duplicate_box(a, b):
    intersection = np.maximum(0, np.minimum(a[2:], b[2:]) - np.maximum(a[:2], b[:2]))
    containment = float(np.prod(intersection)) / max(
        min(float(np.prod(a[2:] - a[:2])), float(np.prod(b[2:] - b[:2]))), 1e-8
    )
    return overlap(a, b) > 0.35 or containment > 0.8


def tile_starts(length, side=960, step=768):
    if length <= side:
        return [0]
    return sorted(set([*range(0, length - side + 1, step), length - side]))


def detect_all(
    models,
    image,
    maximum,
    cancel,
    *,
    detection_threshold=None,
    min_face_size=DEFAULT_MIN_FACE_SIZE,
):
    height, width = image.shape[:2]
    regions = [(0, 0, width, height)]
    if width > 960 or height > 960:
        regions += [
            (x, y, min(x + 960, width), min(y + 960, height))
            for y in tile_starts(height)
            for x in tile_starts(width)
        ]
    faces = []
    for x, y, right, bottom in regions:
        if cancel.is_set():
            raise VideoTestError("cancelled")
        # Remove the live model's ten-face cap. No YOLO person ROI or sampling cap.
        options = (
            {"score_threshold": detection_threshold} if detection_threshold is not None else {}
        )
        detected = models.detect(image[y:bottom, x:right], side=640, max_faces=None, **options)
        for detected_face in detected:
            box = np.asarray(detected_face["bbox"], dtype=np.float64) + [x, y, x, y]
            points = np.asarray(detected_face["landmarks"], dtype=np.float64) + [x, y]
            if not np.isfinite(box).all() or not np.isfinite(points).all():
                continue
            box[:2] = np.maximum(box[:2], 0)
            box[2:] = np.minimum(box[2:], [width, height])
            if min(box[2:] - box[:2]) < min_face_size:
                continue
            faces.append(detected_face | {"bbox": box, "landmarks": points})
    selected = []
    for face in sorted(faces, key=lambda face: face["confidence"], reverse=True):
        if any(duplicate_box(face["bbox"], prior["bbox"]) for prior in selected):
            continue
        selected.append(face)
        if len(selected) > maximum:
            raise VideoTestError("face_limit_exceeded")
    return selected


@dataclass
class ExtractedFace:
    bbox: np.ndarray
    jpeg: bytes
    embedding: np.ndarray | None
    quality: float


def analyze_frame(
    analyzer,
    image,
    maximum,
    cancel,
    *,
    detection_threshold=None,
    min_face_size=DEFAULT_MIN_FACE_SIZE,
):
    faces = []
    height, width = image.shape[:2]
    for face in detect_all(
        analyzer.models,
        image,
        maximum,
        cancel,
        detection_threshold=detection_threshold,
        min_face_size=min_face_size,
    ):
        if cancel.is_set():
            raise VideoTestError("cancelled")
        box = face["bbox"]
        x1, y1 = np.floor(box[:2]).astype(int)
        x2, y2 = np.ceil(box[2:]).astype(int)
        x2, y2 = min(x2, width), min(y2, height)
        gray = cv2.cvtColor(image[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
        blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        embedding = None
        try:
            aligned, residual = align_face(image, face["landmarks"])
            # Display even quality-rejected faces. Only plausible alignments are compared.
            if residual <= 12 and min(x2 - x1, y2 - y1) >= 16:
                embedding = normalized_embedding(analyzer.embed(aligned))
        except (ValueError, np.linalg.LinAlgError):
            pass
        padding = round(max(x2 - x1, y2 - y1) * 0.18)
        crop = image[
            max(0, y1 - padding) : min(height, y2 + padding),
            max(0, x1 - padding) : min(width, x2 + padding),
        ]
        scale = min(1, 256 / max(crop.shape[:2]))
        if scale < 1:
            crop = cv2.resize(
                crop, (max(1, round(crop.shape[1] * scale)), max(1, round(crop.shape[0] * scale)))
            )
        ok, encoded = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not ok:
            raise VideoTestError("image_encoding_failed")
        rank = float(face["confidence"]) * math.log1p(blur) * math.sqrt((x2 - x1) * (y2 - y1))
        faces.append(ExtractedFace(box, encoded.tobytes(), embedding, rank))
    return faces


@dataclass
class FaceGroup:
    metadata: dict
    centroid: np.ndarray | None
    exemplars: list[np.ndarray] = field(default_factory=list)
    last_box: np.ndarray | None = None
    best_quality: float = -1
    cooccurs: int = 0


def add_exemplar(group, embedding):
    # Bounded diversity, not frequency, controls the cluster prototype.
    if not group.exemplars or max(float(v @ embedding) for v in group.exemplars) < 0.98:
        group.exemplars.append(embedding.copy())
        if len(group.exemplars) > 8:
            group.exemplars.pop(0)
        mean = np.mean(group.exemplars, axis=0)
        group.centroid = normalized_embedding(mean) if np.linalg.norm(mean) > 1e-8 else None


class FaceGroups:
    def __init__(self, threshold, maximum):
        self.threshold, self.maximum = threshold, maximum
        self.groups: list[FaceGroup] = []

    def add_frame(self, faces, frame_id, timestamp):
        updated = []
        observed = []
        for face in faces:
            eligible = [g for g in self.groups if g.metadata["last_frame"] != frame_id]
            scores = [
                (float(g.centroid @ face.embedding), g)
                for g in eligible
                if g.centroid is not None and face.embedding is not None
            ]
            # Compare a cluster prototype rather than a single nearest exemplar.
            scores.sort(key=lambda entry: entry[0], reverse=True)
            group = scores[0][1] if scores and scores[0][0] >= self.threshold else None
            if group is None and face.embedding is None:
                spatial = [
                    g
                    for g in eligible
                    if frame_id - g.metadata["last_frame"] <= 2
                    and g.last_box is not None
                    and overlap(face.bbox, g.last_box) >= 0.5
                ]
                group = max(spatial, key=lambda g: overlap(face.bbox, g.last_box), default=None)
            if group is None:
                if len(self.groups) >= self.maximum:
                    raise VideoTestError("group_limit_exceeded")
                group = FaceGroup(
                    {
                        "group_id": len(self.groups) + 1,
                        "occurrences": 0,
                        "first_seen_seconds": round(timestamp, 3),
                        "last_seen_seconds": round(timestamp, 3),
                        "last_frame": frame_id,
                        "best_frame": frame_id,
                        "best_seen_seconds": round(timestamp, 3),
                        "embedding_ready": False,
                    },
                    None,
                )
                self.groups.append(group)
            group.metadata["occurrences"] += 1
            group.metadata.update(last_frame=frame_id, last_seen_seconds=round(timestamp, 3))
            group.last_box = face.bbox.copy()
            if face.embedding is not None:
                add_exemplar(group, face.embedding)
                group.metadata["embedding_ready"] = True
            if face.quality > group.best_quality:
                group.best_quality = face.quality
                group.metadata.update(best_frame=frame_id, best_seen_seconds=round(timestamp, 3))
                updated.append((group.metadata["group_id"], face.jpeg))
            observed.append(group)
        # A bounded bit mask records which groups ever appeared in the same frame.
        mask = sum(1 << (g.metadata["group_id"] - 1) for g in observed)
        for group in observed:
            group.cooccurs |= mask ^ (1 << (group.metadata["group_id"] - 1))
        return updated

    def merge_similar(self, cancel):
        """Complete-link merging of final prototypes; never mutate published groups."""
        groups = [
            replace(g, metadata=dict(g.metadata), exemplars=list(g.exemplars)) for g in self.groups
        ]
        sources = [g.metadata["group_id"] for g in groups]
        if cancel.is_set():
            raise VideoTestError("cancelled")
        if len(groups) < 2:
            return groups, dict(zip(sources, sources, strict=True))
        vectors = np.stack(
            [g.centroid if g.centroid is not None else np.zeros(512, np.float32) for g in groups]
        )
        scores = np.clip(vectors @ vectors.T, -1, 1)
        for i, group in enumerate(groups):
            if group.centroid is None:
                scores[i, :] = scores[:, i] = -np.inf
            for j, other in enumerate(groups):
                if group.cooccurs & (1 << (other.metadata["group_id"] - 1)):
                    scores[i, j] = scores[j, i] = -np.inf
        np.fill_diagonal(scores, -np.inf)
        retained = np.ones(len(groups), dtype=bool)
        while True:
            if cancel.is_set():
                raise VideoTestError("cancelled")
            i, j = np.unravel_index(int(np.argmax(scores)), scores.shape)
            if scores[i, j] < self.threshold:
                break
            target, other = groups[i], groups[j]
            target.metadata["occurrences"] += other.metadata["occurrences"]
            target.metadata["first_seen_seconds"] = min(
                target.metadata["first_seen_seconds"], other.metadata["first_seen_seconds"]
            )
            target.metadata["last_seen_seconds"] = max(
                target.metadata["last_seen_seconds"], other.metadata["last_seen_seconds"]
            )
            if other.metadata["last_frame"] > target.metadata["last_frame"]:
                target.metadata["last_frame"] = other.metadata["last_frame"]
                target.last_box = other.last_box
            if other.best_quality > target.best_quality:
                target.best_quality = other.best_quality
                sources[i] = sources[j]
                for key in ("best_frame", "best_seen_seconds"):
                    target.metadata[key] = other.metadata[key]
            for exemplar in other.exemplars:
                add_exemplar(target, exemplar)
            target.cooccurs |= other.cooccurs
            # Every original prototype pair must pass. A-B-C similarity chains
            # and any co-occurrence conflict therefore cannot collapse into one.
            row = np.minimum(scores[i, :], scores[j, :])
            scores[i, :] = scores[:, i] = row
            scores[j, :] = scores[:, j] = -np.inf
            scores[i, i] = -np.inf
            retained[j] = False
        return (
            [g for i, g in enumerate(groups) if retained[i]],
            {g.metadata["group_id"]: sources[i] for i, g in enumerate(groups) if retained[i]},
        )

    def metadata(self):
        return [dict(group.metadata) for group in self.groups]
