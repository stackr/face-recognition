"""Ephemeral track faces and reference candidate comparisons."""

import threading
from collections import Counter
from dataclasses import dataclass

import cv2
import numpy as np

from app.schemas.recognition import sample_window
from app.worker.face_onnx import MODEL_VERSION, align_face, normalized_embedding


@dataclass
class FaceCandidate:
    metadata: dict
    aligned: np.ndarray | None = None


def quality(image, face, settings, pose=None):
    height, width = image.shape[:2]
    bbox = np.asarray(face["bbox"], dtype=np.float64)
    points = np.asarray(face["landmarks"], dtype=np.float64)
    x1, y1 = np.floor(np.maximum(bbox[:2], 0)).astype(int)
    x2, y2 = np.ceil(np.minimum(bbox[2:], [width, height])).astype(int)
    metadata = {
        "status": "rejected",
        "reasons": [],
        "bbox": np.round(bbox, 1).tolist(),
        "landmarks": np.round(points, 1).tolist(),
        "confidence": round(face["confidence"], 4),
        "face_size": [int(max(0, x2 - x1)), int(max(0, y2 - y1))],
        "quality": 0.0,
        "blur_score": None,
        "brightness": None,
        "yaw": None,
        "pitch": None,
        "roll": None,
        "pose_status": "unavailable",
        "occlusion_check": "landmark_geometry_heuristic",
    }
    reasons = metadata["reasons"]
    if x2 <= x1 or y2 <= y1:
        reasons.append("invalid_face_box")
        return FaceCandidate(metadata)
    if np.any(bbox[:2] < 0) or np.any(bbox[2:] > [width, height]):
        reasons.append("face_clipped")
    size = min(x2 - x1, y2 - y1)
    if size < settings.face_min_size:
        reasons.append("face_too_small")
    gray = cv2.cvtColor(image[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
    blur, brightness = float(cv2.Laplacian(gray, cv2.CV_64F).var()), float(gray.mean())
    metadata.update(blur_score=round(blur, 2), brightness=round(brightness, 2))
    if blur < settings.face_min_blur:
        reasons.append("blurred")
    if brightness < 35 or float(np.mean(gray <= 10)) > 0.5:
        reasons.append("too_dark")
    if brightness > 220 or float(np.mean(gray >= 245)) > 0.5:
        reasons.append("too_bright")
    if face["confidence"] < settings.face_detection_threshold:
        reasons.append("low_confidence")
    aligned = None
    try:
        aligned, residual = align_face(image, points)
        metadata["alignment_error"] = round(residual, 3)
        eyes = float(np.linalg.norm(points[1] - points[0]))
        inside = ((points >= bbox[:2] - 5) & (points <= bbox[2:] + 5)).all()
        if (
            residual > 8
            or eyes < size * 0.12
            or not inside
            or points[0, 0] >= points[1, 0]
            or points[3, 0] >= points[4, 0]
        ):
            reasons.append("landmark_geometry")
    except (ValueError, np.linalg.LinAlgError):
        reasons.append("landmark_geometry")
    pose_score = 0.0
    if pose is not None:
        metadata.update({key: round(pose[key], 2) for key in ("yaw", "pitch", "roll")})
        metadata["pose_status"] = "estimated_3d_landmarks"
        ratios = [
            abs(pose[key]) / limit
            for key, limit in (
                ("yaw", settings.face_max_yaw),
                ("pitch", settings.face_max_pitch),
                ("roll", settings.face_max_roll),
            )
        ]
        if max(ratios) > 1:
            reasons.append("pose_exceeded")
        pose_score = max(0, 1 - max(ratios) * 0.5)
    else:
        reasons.append("pose_unavailable")
    score = (
        0.25 * min(size / (settings.face_min_size * 1.5), 1)
        + 0.25 * min(blur / (settings.face_min_blur * 2), 1)
        + 0.15 * max(0, 1 - abs(brightness - 127.5) / 127.5)
        + 0.2 * pose_score
        + 0.15 * min(max(face["confidence"], 0), 1)
    )
    metadata["quality"] = round(score, 4)
    if score < settings.face_quality_threshold:
        reasons.append("quality_below_threshold")
    if not reasons:
        metadata["status"] = "accepted"
    return FaceCandidate(metadata, aligned if not reasons else None)


class FaceAnalyzer:
    def __init__(self, settings, models=None):
        from app.worker.face_onnx import FaceModels

        self.settings = settings
        self.models = models or FaceModels(settings)
        self.info = self.models.info | {
            "quality": {
                "min_face_size": settings.face_min_size,
                "min_blur": settings.face_min_blur,
                "threshold": settings.face_quality_threshold,
                "max_yaw": settings.face_max_yaw,
                "max_pitch": settings.face_max_pitch,
                "max_roll": settings.face_max_roll,
                "brightness_range": [35, 220],
                "calibrated": False,
                "interval_seconds": settings.face_analysis_interval,
                "max_rois_per_frame": settings.face_rois_per_frame,
            }
        }

    def inspect(self, image, person_bbox):
        height, width = image.shape[:2]
        x1, y1 = np.floor(np.maximum(person_bbox[:2], 0)).astype(int)
        x2, y2 = np.ceil(np.minimum(person_bbox[2:], [width, height])).astype(int)
        if x2 <= x1 or y2 <= y1:
            return FaceCandidate({"status": "no_face", "reasons": ["no_face"], "quality": 0})
        # Preserve the original person association boundary even with crop padding.
        person = (x1, y1, x2, y2)
        pw, ph = x2 - x1, y2 - y1
        head = (
            max(0, x1 - round(pw * 0.05)),
            max(0, y1 - round(ph * 0.05)),
            min(width, x2 + round(pw * 0.05)),
            min(height, y1 + round(ph * 0.5)),
        )
        candidate = self.inspect_region(image, person, head, 320, "head", 1)
        if candidate.metadata["status"] in {"accepted", "ambiguous"}:
            return candidate
        retry_reasons = {"face_too_small", "face_clipped", "landmark_geometry"}
        if candidate.metadata["status"] != "no_face" and not retry_reasons.intersection(
            candidate.metadata["reasons"]
        ):
            return candidate
        candidate = self.inspect_region(image, person, person, 320, "person", 2)
        if candidate.metadata["status"] == "no_face" or retry_reasons.intersection(
            candidate.metadata["reasons"]
        ):
            candidate = self.inspect_region(image, person, person, 640, "person", 3)
        return candidate

    def inspect_region(self, image, person, region, side, name, passes):
        x1, y1, x2, y2 = region
        px1, py1, px2, py2 = person
        roi = image[y1:y2, x1:x2]
        detected = self.models.detect(roi, side=side)
        details = {"detector_region": name, "detector_input": side, "detection_passes": passes}
        eligible = []
        for face in detected:
            bbox = face["bbox"]
            center = (bbox[:2] + bbox[2:]) / 2 + [x1, y1]
            # A lower-body/neighbor face is not assigned to this person's track.
            if px1 <= center[0] <= px2 and py1 <= center[1] <= py1 + (py2 - py1) * 0.65:
                eligible.append(face)
        if not eligible:
            return FaceCandidate(
                {"status": "no_face", "reasons": ["no_face"], "quality": 0} | details
            )
        if len(eligible) > 1:
            return FaceCandidate(
                {"status": "ambiguous", "reasons": ["multiple_faces"], "quality": 0} | details
            )
        face = eligible[0]
        if np.any(face["bbox"][:2] < 0) or np.any(face["bbox"][2:] > [roi.shape[1], roi.shape[0]]):
            return FaceCandidate(
                {"status": "rejected", "reasons": ["face_clipped"], "quality": 0} | details
            )
        face = face | {
            "bbox": face["bbox"] + [x1, y1, x1, y1],
            "landmarks": face["landmarks"] + [x1, y1],
        }
        # Tiny faces cannot yield a reliable pose and are rejected without 3D inference.
        pose = None
        if min(face["bbox"][2:] - face["bbox"][:2]) >= self.settings.face_min_size:
            pose = self.models.pose(image, face["bbox"])
        candidate = quality(image, face, self.settings, pose)
        candidate.metadata.update(details)
        return candidate

    def embed(self, aligned):
        return self.models.embed(aligned)

    def inspect_reference(self, image):
        detected = self.models.detect(image)
        if len(detected) != 1:
            code = "no_face" if not detected else "multiple_faces"
            return FaceCandidate({"status": code, "reasons": [code], "quality": 0})
        face = detected[0]
        pose = (
            self.models.pose(image, face["bbox"])
            if min(face["bbox"][2:] - face["bbox"][:2]) >= self.settings.face_min_size
            else None
        )
        return quality(image, face, self.settings, pose)


class TrackFaces:
    def __init__(self, settings):
        self.settings = settings
        self.lock = threading.RLock()
        self.tracks = {}

    def process(self, analyzer, frame, tracks, live_ids):
        counts = Counter()
        event_frame_jpeg = None
        with self.lock:
            self.tracks = {
                key: value
                for key, value in self.tracks.items()
                if key in live_ids
                and value["stream_session_id"] == frame.stream_session_id
                and frame.captured_mono - value["last_seen_mono"]
                <= self.settings.track_lost_seconds
            }
            for track in tracks:
                key = track["track_id"]
                if (
                    key not in self.tracks
                    and len(self.tracks) < self.settings.face_tracks_per_camera
                ):
                    self.tracks[key] = {
                        "last_attempt": float("-inf"),
                        "last_seen_mono": frame.captured_mono,
                        "first_seen_at": frame.captured_at,
                        "last_seen_at": frame.captured_at,
                        "current": {"status": "pending", "quality": 0, "reasons": []},
                        "best": None,
                        "samples": [],
                        "stream_session_id": frame.stream_session_id,
                    }
                if key in self.tracks:
                    state = self.tracks[key]
                    retained = [
                        sample
                        for sample in state["samples"]
                        if sample["stream_session_id"] == frame.stream_session_id
                        and frame.captured_mono - sample["captured_mono"]
                        <= sample_window(self.settings)
                    ]
                    state["samples"] = retained
                    if not retained:
                        state["match_samples"] = {}
                        state["matches"] = []
                        state.pop("match_key", None)
                        state.pop("comparison", None)
                    state["best"] = max(
                        retained, key=lambda sample: sample["quality"], default=None
                    )
                    self.tracks[key].update(
                        last_seen_mono=frame.captured_mono, last_seen_at=frame.captured_at
                    )
            due = sorted(
                [
                    track
                    for track in tracks
                    if track["track_id"] in self.tracks
                    and frame.captured_mono - self.tracks[track["track_id"]]["last_attempt"]
                    >= self.settings.face_analysis_interval
                ],
                key=lambda track: self.tracks[track["track_id"]]["last_attempt"],
            )[: self.settings.face_rois_per_frame]
        for track in due:
            key = track["track_id"]
            candidate = analyzer.inspect(frame.image, track["bbox"])
            counts["roi_attempts"] += 1
            if candidate.metadata["status"] not in {"no_face", "ambiguous"}:
                counts["faces_detected"] += 1
            with self.lock:
                state = self.tracks[key]
                state["last_attempt"] = frame.captured_mono
                state["current"] = candidate.metadata | {
                    "frame_id": frame.frame_id,
                    "captured_at": frame.captured_at,
                }
            if candidate.metadata["status"] != "accepted":
                counts["quality_rejected"] += 1
                continue
            counts["quality_accepted"] += 1
            # Qualified later faces are compared even when their quality is lower.
            vector = normalized_embedding(analyzer.embed(candidate.aligned))
            ok, jpeg = cv2.imencode(".jpg", candidate.aligned, [cv2.IMWRITE_JPEG_QUALITY, 90])
            if not ok:
                raise RuntimeError("Face thumbnail encoding failed")
            if event_frame_jpeg is None:
                image = frame.image
                scale = min(1, 1280 / max(image.shape[:2]))
                if scale < 1:
                    image = cv2.resize(
                        image, (round(image.shape[1] * scale), round(image.shape[0] * scale))
                    )
                frame_ok, frame_jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if not frame_ok:
                    raise RuntimeError("Event frame encoding failed")
                event_frame_jpeg = frame_jpeg.tobytes()
            with self.lock:
                if (
                    state["samples"]
                    and max(float(previous["embedding"] @ vector) for previous in state["samples"])
                    < self.settings.face_track_consistency_threshold
                ):
                    # An abrupt identity change must not inherit earlier consensus.
                    state["samples"] = []
                    state.pop("match_key", None)
                    counts["sample_resets"] += 1
                sample = {
                    "quality": candidate.metadata["quality"],
                    "embedding": vector,
                    "jpeg": jpeg.tobytes(),
                    "frame_jpeg": event_frame_jpeg,
                    "frame_id": frame.frame_id,
                    "captured_at": frame.captured_at,
                    "stream_session_id": frame.stream_session_id,
                    "model_version": MODEL_VERSION,
                    "captured_mono": frame.captured_mono,
                }
                state["samples"].append(sample)
                state["samples"] = state["samples"][-self.settings.face_sample_count :]
                state["best"] = max(state["samples"], key=lambda sample: sample["quality"])
            counts["embeddings_created"] += 1
        counts["analysis_frames"] = int(bool(due))
        with self.lock:
            for track in tracks:
                state = self.tracks.get(track["track_id"])
                if state is None:
                    track["face"] = {
                        "status": "capacity",
                        "quality": 0,
                        "reasons": ["cache_capacity"],
                        "embedding_ready": False,
                    }
                    continue
                best = state["best"]
                track["first_seen_at"], track["last_seen_at"] = (
                    state["first_seen_at"],
                    state["last_seen_at"],
                )
                track["face"] = state["current"] | {
                    "embedding_ready": best is not None,
                    "sample_count": len(state["samples"]),
                    "best": {
                        key: best[key]
                        for key in ("quality", "frame_id", "captured_at", "model_version")
                    }
                    if best
                    else None,
                }
        return counts

    def thumbnail(self, track_id, session, now):
        with self.lock:
            state = self.tracks.get(track_id)
            if state is None or now - state["last_seen_mono"] > self.settings.track_lost_seconds:
                return None
            best = state["best"]
            return best["jpeg"] if best and best["stream_session_id"] == session else None

    def match(self, gallery, tracks):
        key, _ = gallery.snapshot()
        with self.lock:
            for track in tracks:
                state = self.tracks.get(track["track_id"])
                samples = state["samples"] if state else []
                face = track.get("face")
                if face is None:
                    continue
                if not samples:
                    face["matches"] = []
                    continue
                threshold = self.settings.face_match_threshold
                match_key = (key, threshold, tuple(sample["frame_id"] for sample in samples))
                if state.get("match_key") != match_key:
                    scored = []
                    for sample in samples:
                        if sample.get("search_key") != key:
                            sample["search"] = gallery.search(
                                sample["embedding"], limit=self.settings.max_target_persons
                            )
                            sample["search_key"] = key
                        scored.append(sample["search"])
                    weights = [max(sample["quality"], 0.01) for sample in samples]
                    persons = {}
                    for result in scored:
                        for item in result["matches"]:
                            persons[item["person_id"]] = item
                    combined, evidence = [], {}
                    for person_id, item in persons.items():
                        matches = [
                            next(
                                (
                                    match
                                    for match in result["matches"]
                                    if match["person_id"] == person_id
                                ),
                                None,
                            )
                            for result in scored
                        ]
                        scores = [match["similarity"] if match else -1 for match in matches]
                        score = float(np.average(scores, weights=weights))
                        supporting = [
                            sample
                            for sample, value, match in zip(samples, scores, matches, strict=True)
                            if match is not None and value >= threshold
                        ]
                        combined.append(
                            item | {"similarity": score, "supporting_samples": len(supporting)}
                        )
                        if supporting:
                            chosen = max(supporting, key=lambda sample: sample["quality"])
                            chosen_match = next(
                                match
                                for sample, match in zip(samples, matches, strict=True)
                                if sample is chosen
                            )
                            evidence[person_id] = chosen
                            combined[-1]["face_id"] = chosen_match["face_id"]
                    combined.sort(key=lambda item: (-item["similarity"], item["person_id"]))
                    state["matches"] = [
                        item
                        for item in combined
                        if item["similarity"] >= threshold and item["supporting_samples"] >= 2
                    ]
                    state["match_samples"] = evidence
                    state["comparison"] = {
                        "outcome": "gallery_empty"
                        if not persons
                        else "collecting_samples"
                        if len(samples) < 2
                        else "matched"
                        if state["matches"]
                        else "insufficient_consensus"
                        if combined and combined[0]["similarity"] >= threshold
                        else "below_threshold",
                        "threshold": threshold,
                        "top_similarity": combined[0]["similarity"] if combined else None,
                        "supporting_samples": combined[0]["supporting_samples"] if combined else 0,
                        "minimum_samples": 2,
                    }
                    state["match_key"] = match_key
                face["matches"] = state["matches"]
                face["comparison"] = state["comparison"]
        return key[0]

    def size(self):
        with self.lock:
            return len(self.tracks)
