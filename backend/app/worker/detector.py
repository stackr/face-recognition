import hashlib
import logging
import os
from typing import Protocol

import numpy as np

from app.core.config import ROOT


class PersonDetector(Protocol):
    def detect(self, frame): ...


class YoloPersonDetector:
    def __init__(self, settings):
        os.environ.setdefault("YOLO_CONFIG_DIR", str(ROOT / "data/ultralytics"))
        os.environ["YOLO_AUTOINSTALL"] = "false"
        os.environ.setdefault("YOLO_OFFLINE", "true")
        import torch
        from ultralytics import YOLO
        from ultralytics.utils import LOGGER, SETTINGS

        LOGGER.setLevel(logging.ERROR)
        SETTINGS.update({"sync": False})
        path = settings.yolo_model_path.resolve()
        if not path.is_file():
            raise RuntimeError("YOLO weights missing; run scripts/prepare_phase2.py")
        if settings.requested_device == "cuda" and torch.cuda.is_available():
            self.device = "cuda:0"
        elif settings.requested_device == "cpu" or settings.allow_cpu_fallback:
            self.device = "cpu"
        else:
            raise RuntimeError("CUDA required but unavailable")
        torch.set_num_threads(2)
        self.model = YOLO(str(path), task="detect")
        self.confidence = settings.detection_confidence
        self.fp16 = settings.yolo_fp16 and self.device.startswith("cuda")
        self.detect(np.zeros((640, 640, 3), dtype=np.uint8))
        # Recent Ultralytics versions load a separate fused inference backend.
        # Verify the parameters that actually execute, rather than the CPU checkpoint.
        actual = str(next(self.model.predictor.model.model.parameters()).device)
        if actual != self.device:
            raise RuntimeError("Requested and actual detector devices differ")
        self.info = {
            "model": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "actual_device": actual,
            "gpu_name": torch.cuda.get_device_name(0) if actual.startswith("cuda") else None,
            "torch_version": torch.__version__,
            "torch_cuda": torch.version.cuda,
            "precision": "float16" if self.fp16 else "float32",
            "parameter_dtype": str(next(self.model.predictor.model.model.parameters()).dtype),
            "resize": "long edge 640; stride-aligned rectangular padding; mixed shapes square 640",
            "max_batch_size": settings.detector_batch_size,
            "confidence_threshold": self.confidence,
            "status": "passed" if actual.startswith("cuda") else "cpu",
        }

    def configure_confidence(self, value):
        self.confidence = value
        self.info["confidence_threshold"] = value

    def detect(self, frame):
        return self.detect_batch([frame])[0]

    def detect_batch(self, frames):
        if not frames or len(frames) > 4:
            raise ValueError("Batch must contain 1..4 frames")
        results = self.model.predict(
            frames,
            classes=[0],
            conf=self.confidence,
            imgsz=640,
            max_det=100,
            device=self.device,
            quantize=16 if self.fp16 else 32,
            verbose=False,
            save=False,
        )
        return [result.boxes.cpu().numpy() for result in results]

    def resources(self):
        import psutil
        import torch

        rss = round(psutil.Process().memory_info().rss / 2**20, 1)
        if not self.device.startswith("cuda"):
            return {"gpu_allocated_mb": 0, "gpu_reserved_mb": 0, "rss_mb": rss}
        return {
            "rss_mb": rss,
            "gpu_allocated_mb": round(torch.cuda.memory_allocated(0) / 2**20, 1),
            "gpu_reserved_mb": round(torch.cuda.memory_reserved(0) / 2**20, 1),
        }
