"""Direct face detection for uploaded MP4; camera/session-local spatial IDs."""

import math
from itertools import count

import cv2
import numpy as np

from app.worker.faces import FaceCandidate
from app.worker.video_faces import align_detected_face, detect_all, overlap


class FaceBoxTracker:
    def __init__(self, settings):
        self.settings = settings
        self.identifiers = count(1)
        self.seen = {}
        self.confidences = {}

    def update(self, detections, now):
        self.seen = {
            identifier: value
            for identifier, value in self.seen.items()
            if now - value[1] <= self.settings.track_lost_seconds
        }
        self.confidences = {
            key: value for key, value in self.confidences.items() if key in self.seen
        }
        pairs = sorted(
            (
                (overlap(face["bbox"], previous[0]), index, identifier)
                for index, face in enumerate(detections)
                for identifier, previous in self.seen.items()
            ),
            reverse=True,
        )
        assigned, used = {}, set()
        for score, index, identifier in pairs:
            if score >= 0.2 and index not in assigned and identifier not in used:
                assigned[index] = identifier
                used.add(identifier)
        tracks, candidates = [], {}
        for index, face in enumerate(detections):
            identifier = assigned.get(index)
            if identifier is None:
                if len(self.seen) >= self.settings.face_tracks_per_camera:
                    continue
                identifier = next(self.identifiers)
            self.seen[identifier] = (face["bbox"].copy(), now)
            self.confidences[identifier] = round(face["confidence"], 4)
            bbox = np.round(face["bbox"], 1).tolist()
            tracks.append(
                {"track_id": identifier, "bbox": bbox, "confidence": round(face["confidence"], 4)}
            )
            candidates[tuple(bbox)] = face
        return tracks, candidates

    def visible_tracks(self, now):
        return [
            {
                "track_id": identifier,
                "bbox": np.round(value[0], 1).tolist(),
                "confidence": self.confidences.get(identifier, 0),
            }
            for identifier, value in self.seen.items()
            if now - value[1] <= self.settings.track_lost_seconds
        ]

    def live_ids(self):
        return set(self.seen)


class UploadedFaceAnalyzer:
    """Use already detected faces; never run a second detector inside a face box."""

    def __init__(self, analyzer, candidates):
        self.analyzer, self.candidates = analyzer, candidates

    def inspect(self, image, bbox):
        face = self.candidates[tuple(bbox)]
        x1, y1 = np.floor(face["bbox"][:2]).astype(int)
        x2, y2 = np.ceil(face["bbox"][2:]).astype(int)
        gray = cv2.cvtColor(image[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
        blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        rank = face["confidence"] * math.log1p(blur) * math.sqrt((x2 - x1) * (y2 - y1))
        aligned = align_detected_face(image, face)
        metadata = {
            "status": "accepted" if aligned is not None else "rejected",
            "reasons": [] if aligned is not None else ["landmark_geometry"],
            "bbox": bbox,
            "landmarks": np.round(face["landmarks"], 1).tolist(),
            "confidence": round(face["confidence"], 4),
            "face_size": [x2.item() - x1.item(), y2.item() - y1.item()],
            "blur_score": round(blur, 2),
            "brightness": round(float(gray.mean()), 2),
            # Monotonic normalization preserves the test page's best-photo ordering.
            "quality": round(rank / (rank + 100), 4),
            "yaw": None,
            "pitch": None,
            "roll": None,
            "detector_region": "full_frame_tiled",
            "detector_input": 640,
        }
        return FaceCandidate(metadata, aligned)

    def embed(self, aligned):
        return self.analyzer.embed(aligned)


def detect_uploaded_faces(analyzer, frame, settings, cancel):
    return detect_all(
        analyzer.models,
        frame.image,
        settings.face_test_max_faces_per_frame,
        cancel,
        detection_threshold=settings.video_face_detection_threshold,
        min_face_size=settings.video_face_min_size,
    )
