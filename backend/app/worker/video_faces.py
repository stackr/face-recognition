"""All-frame face extraction and ephemeral, per-video appearance clustering."""

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from app.core.face_data import normalized_embedding
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


def detect_all(models, image, maximum, cancel):
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
        detected = models.detect(image[y:bottom, x:right], side=640, max_faces=None)
        for detected_face in detected:
            box = np.asarray(detected_face["bbox"], dtype=np.float64) + [x, y, x, y]
            points = np.asarray(detected_face["landmarks"], dtype=np.float64) + [x, y]
            if not np.isfinite(box).all() or not np.isfinite(points).all():
                continue
            box[:2] = np.maximum(box[:2], 0)
            box[2:] = np.minimum(box[2:], [width, height])
            if min(box[2:] - box[:2]) < 8:
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


def analyze_frame(analyzer, image, maximum, cancel):
    faces = []
    height, width = image.shape[:2]
    for face in detect_all(analyzer.models, image, maximum, cancel):
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


class FaceGroups:
    def __init__(self, threshold, maximum):
        self.threshold, self.maximum = threshold, maximum
        self.groups: list[FaceGroup] = []

    def add_frame(self, faces, frame_id, timestamp):
        updated = []
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
                # Bounded diversity, not frequency, controls the cluster prototype.
                if (
                    not group.exemplars
                    or max(float(v @ face.embedding) for v in group.exemplars) < 0.98
                ):
                    group.exemplars.append(face.embedding.copy())
                    if len(group.exemplars) > 8:
                        group.exemplars.pop(0)
                    mean = np.mean(group.exemplars, axis=0)
                    if np.linalg.norm(mean) > 1e-8:
                        group.centroid = normalized_embedding(mean)
                group.metadata["embedding_ready"] = True
            if face.quality > group.best_quality:
                group.best_quality = face.quality
                group.metadata.update(best_frame=frame_id, best_seen_seconds=round(timestamp, 3))
                updated.append((group.metadata["group_id"], face.jpeg))
        return updated

    def metadata(self):
        return [dict(group.metadata) for group in self.groups]
