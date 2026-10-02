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
            "status": "passed" if actual.startswith("cuda") else "cpu",
        }

    def detect(self, frame):
        result = self.model.predict(
            frame,
            classes=[0],
            conf=self.confidence,
            imgsz=640,
            max_det=100,
            device=self.device,
            verbose=False,
            save=False,
        )[0]
        return result.boxes.cpu().numpy()

    def resources(self):
        import torch

        if not self.device.startswith("cuda"):
            return {"gpu_allocated_mb": 0, "gpu_reserved_mb": 0}
        return {
            "gpu_allocated_mb": round(torch.cuda.memory_allocated(0) / 2**20, 1),
            "gpu_reserved_mb": round(torch.cuda.memory_reserved(0) / 2**20, 1),
        }
