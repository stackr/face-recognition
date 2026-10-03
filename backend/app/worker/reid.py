"""Optional body appearance embeddings. Never assign face identities from clothes."""

import hashlib
import threading
from abc import ABC, abstractmethod
from collections import Counter
from pathlib import Path

import numpy as np

from app.core.reid_data import ARCHITECTURE_SHA256, MODEL_VERSION, WEIGHTS_SHA256


def body_embedding(vector):
    vector = np.asarray(vector, dtype=np.float32)
    if vector.shape != (512,) or not np.isfinite(vector).all():
        raise ValueError("Invalid body embedding")
    norm = float(np.linalg.norm(vector))
    if norm < 1e-8:
        raise ValueError("Invalid body embedding norm")
    return vector / norm


class PersonReIdentifier(ABC):
    @abstractmethod
    def extract_embedding(self, person_crop):
        """Return a private L2-normalized body embedding."""

    def compare(self, embedding_a, embedding_b):
        return float(np.clip(body_embedding(embedding_a) @ body_embedding(embedding_b), -1, 1))


class DisabledReIdentifier(PersonReIdentifier):
    info = {"status": "disabled"}

    def extract_embedding(self, person_crop):
        raise RuntimeError("Person Re-ID disabled")


class OSNetReIdentifier(PersonReIdentifier):
    def __init__(self, settings):
        import torch

        architecture = Path(__file__).with_name("vendor") / "osnet.py"
        if hashlib.sha256(architecture.read_bytes()).hexdigest() != ARCHITECTURE_SHA256:
            raise RuntimeError("OSNet architecture checksum mismatch")
        path = settings.reid_model_path
        if not path.is_file():
            raise FileNotFoundError("OSNet weights missing; run scripts/prepare_reid.py")
        if hashlib.sha256(path.read_bytes()).hexdigest() != WEIGHTS_SHA256:
            raise ValueError("OSNet model checksum mismatch")
        if settings.requested_device == "cuda" and torch.cuda.is_available():
            self.device = "cuda:0"
        elif settings.requested_device == "cpu" or settings.allow_cpu_fallback:
            self.device = "cpu"
        else:
            raise RuntimeError("Re-ID CUDA required but unavailable")
        from app.worker.vendor.osnet import osnet_x0_25

        state = torch.load(path, map_location="cpu", weights_only=True)
        state = state.get("state_dict", state)
        state = {k.removeprefix("module."): v for k, v in state.items()}
        state = {k: v for k, v in state.items() if not k.startswith("classifier.")}
        self.model = osnet_x0_25(num_classes=1, pretrained=False)
        incompatible = self.model.load_state_dict(state, strict=False)
        if (
            set(incompatible.missing_keys) != {"classifier.weight", "classifier.bias"}
            or incompatible.unexpected_keys
        ):
            raise ValueError("OSNet feature weights incomplete")
        self.model.to(self.device).eval()
        actual = str(next(self.model.parameters()).device)
        if actual != self.device:
            raise RuntimeError("Requested and actual Re-ID devices differ")
        self.mean = torch.tensor([0.485, 0.456, 0.406], device=self.device).view(3, 1, 1)
        self.std = torch.tensor([0.229, 0.224, 0.225], device=self.device).view(3, 1, 1)
        self.extract_embedding(np.zeros((256, 128, 3), np.uint8))
        self.info = {
            "status": "ready",
            "model_version": MODEL_VERSION,
            "sha256": WEIGHTS_SHA256,
            "actual_device": actual,
            "training_dataset": "MSMT17 combineall",
            "embedding_dimension": 512,
            "input_size": [256, 128],
            "normalization": "RGB/ImageNet mean+std -> L2 body embedding",
            "precision": "float32",
            "identity_assignment": False,
            "accuracy_calibrated": False,
        }

    def extract_embedding(self, person_crop):
        import torch
        from PIL import Image

        image = np.asarray(person_crop)
        if (
            image.dtype != np.uint8
            or image.ndim != 3
            or image.shape[2] != 3
            or image.shape[0] < 64
            or image.shape[1] < 32
            or image.shape[0] > 2160
            or image.shape[1] > 3840
        ):
            raise ValueError("Invalid BGR person crop")
        rgb = Image.fromarray(image[:, :, ::-1]).resize((128, 256), Image.Resampling.BILINEAR)
        array = np.asarray(rgb, dtype=np.float32).transpose(2, 0, 1).copy() / 255
        with torch.inference_mode():
            tensor = torch.from_numpy(array).to(self.device)
            features = self.model(((tensor - self.mean) / self.std).unsqueeze(0))
        return body_embedding(features[0].detach().cpu().numpy())


class BodyTrackCache:
    """Per-camera/session, bounded RAM only. No vectors or body images in API results."""

    def __init__(self, settings):
        self.settings = settings
        self.lock = threading.RLock()
        self.tracks = {}

    def process(self, model, frame, tracks, live_ids):
        counts = Counter()
        with self.lock:
            self.tracks = {
                key: value
                for key, value in self.tracks.items()
                if key in live_ids
                and value["session_id"] == frame.stream_session_id
                and frame.captured_mono - value["last_seen"] <= self.settings.track_lost_seconds
            }
            for track in tracks:
                key = track["track_id"]
                if (
                    key not in self.tracks
                    and len(self.tracks) < self.settings.reid_tracks_per_camera
                ):
                    self.tracks[key] = {
                        "last_seen": frame.captured_mono,
                        "last_attempt": -float("inf"),
                        "session_id": frame.stream_session_id,
                        "embedding": None,
                        "metadata": {"status": "pending", "embedding_ready": False},
                    }
                if key in self.tracks:
                    self.tracks[key]["last_seen"] = frame.captured_mono
            due = sorted(
                [
                    t
                    for t in tracks
                    if t["track_id"] in self.tracks
                    and frame.captured_mono - self.tracks[t["track_id"]]["last_attempt"]
                    >= self.settings.reid_interval_seconds
                ],
                key=lambda t: self.tracks[t["track_id"]]["last_attempt"],
            )[: self.settings.reid_rois_per_frame]
        for track in due:
            state = self.tracks[track["track_id"]]
            state["last_attempt"] = frame.captured_mono
            counts["roi_attempts"] += 1
            h, w = frame.image.shape[:2]
            x1, y1 = np.floor(np.maximum(track["bbox"][:2], 0)).astype(int)
            x2, y2 = np.ceil(np.minimum(track["bbox"][2:], [w, h])).astype(int)
            metadata = {
                "status": "rejected",
                "embedding_ready": False,
                "model_version": model.info["model_version"],
                "frame_id": frame.frame_id,
                "captured_at": frame.captured_at,
                "identity_assignment": False,
            }
            try:
                if x2 - x1 < 32 or y2 - y1 < 64:
                    counts["small_body"] += 1
                    metadata["reason"] = "body_too_small"
                else:
                    vector = body_embedding(model.extract_embedding(frame.image[y1:y2, x1:x2]))
                    with self.lock:
                        state["embedding"] = vector
                    metadata.update(status="ready", embedding_ready=True, embedding_dimension=512)
                    counts["embeddings_created"] += 1
            except Exception:
                # An optional body model failure never interrupts face events/search.
                metadata.update(status="unavailable", reason="reid_inference_failed")
                counts["failures"] += 1
            with self.lock:
                if metadata["status"] != "ready":
                    state["embedding"] = None
                state["metadata"] = metadata
        with self.lock:
            for track in tracks:
                state = self.tracks.get(track["track_id"])
                track["reid"] = (
                    dict(state["metadata"])
                    if state
                    else {"status": "capacity", "embedding_ready": False}
                )
        return counts

    def size(self):
        with self.lock:
            return len(self.tracks)
