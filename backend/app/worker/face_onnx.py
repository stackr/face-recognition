"""Pinned buffalo_l ONNX protocol adapters; no InsightFace Python dependency.

Tensor decoding, ArcFace template and 3D landmark conventions follow InsightFace
v0.7 (MIT, copyright (c) 2022 Jiankang Deng and Jia Guo). See third_party/insightface/LICENSE.
Pretrained weights have separate non-commercial research terms.
"""

import hashlib
import json
import math
import tempfile
from pathlib import Path

import cv2
import numpy as np
import onnx
import onnxruntime as ort

MODEL_HASHES = {
    "det_10g.onnx": "5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91",
    "w600k_r50.onnx": "4c06341c33c2ca1f86781dab0e829f88ad5b64be9fba56e56bc9ebdefc619e43",
    "1k3d68.onnx": "df5c06b8a0c12e422b2ed8947b8869faa4105387f199c477af038aa01f9a45cc",
    "meanshape_68.npy": "e32b77ecb2a39112eea88e727bd39db5319f5593590cecebcd3955d0b2d2257c",
}
TEMPLATE = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float64,
)
MODEL_VERSION = "buffalo_l-v0.7-w600k_r50-" + MODEL_HASHES["w600k_r50.onnx"][:12]


def sha256(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(2**20), b""):
            result.update(chunk)
    return result.hexdigest()


def similarity_matrix(points):
    """Least-squares similarity transform (translation, rotation, uniform scale)."""
    points = np.asarray(points, dtype=np.float64)
    if points.shape != (5, 2) or not np.isfinite(points).all():
        raise ValueError("Invalid five-point landmarks")
    # [a -b tx; b a ty] avoids stochastic robust-estimator results.
    design = np.zeros((10, 4))
    design[0::2, 0], design[0::2, 1], design[0::2, 2] = points[:, 0], -points[:, 1], 1
    design[1::2, 0], design[1::2, 1], design[1::2, 3] = points[:, 1], points[:, 0], 1
    solution, _, rank, _ = np.linalg.lstsq(design, TEMPLATE.ravel(), rcond=None)
    if rank != 4 or not np.isfinite(solution).all():
        raise ValueError("Degenerate landmarks")
    a, b, tx, ty = solution
    if math.hypot(a, b) < 1e-5:
        raise ValueError("Degenerate alignment scale")
    matrix = np.array([[a, -b, tx], [b, a, ty]], dtype=np.float32)
    mapped = points @ matrix[:, :2].T + matrix[:, 2]
    residual = float(np.sqrt(np.mean(np.square(mapped - TEMPLATE))))
    return matrix, residual


def align_face(image, landmarks):
    matrix, residual = similarity_matrix(landmarks)
    return cv2.warpAffine(image, matrix, (112, 112), borderValue=0), residual


def normalized_embedding(value):
    value = np.asarray(value, dtype=np.float32).reshape(-1)
    if value.shape != (512,) or not np.isfinite(value).all():
        raise ValueError("Invalid ArcFace embedding")
    norm = float(np.linalg.norm(value))
    if norm < 1e-8:
        raise ValueError("Zero ArcFace embedding")
    return value / norm


class FaceModels:
    """One shared model set. CUDA proof uses executed convolution profile events."""

    def __init__(self, settings):
        self.settings = settings
        directory = settings.face_model_dir
        for name, expected in MODEL_HASHES.items():
            if sha256(directory / name) != expected:
                raise RuntimeError("Face model checksum mismatch")
        self.mean_shape = np.load(directory / "meanshape_68.npy", allow_pickle=False)
        if self.mean_shape.shape != (68, 3) or not np.isfinite(self.mean_shape).all():
            raise ValueError("Invalid pose template")
        self.device = settings.requested_device
        self.sessions, evidence = {}, {}
        if self.device == "cuda":
            import torch  # Preload the pinned CUDA/cuDNN runtime before ONNX Runtime.

            if not torch.cuda.is_available():
                if not settings.allow_cpu_fallback:
                    raise RuntimeError("Requested face CUDA device unavailable")
                self.device = "cpu"
            else:
                ort.preload_dlls(cuda=True, cudnn=True, msvc=False)
        providers = ["CPUExecutionProvider"]
        if self.device == "cuda":
            providers.insert(
                0,
                (
                    "CUDAExecutionProvider",
                    {
                        "device_id": 0,
                        "gpu_mem_limit": 1024 * 2**20,
                        "use_ep_level_unified_stream": "1",
                        "cudnn_conv_use_max_workspace": "0",
                        "arena_extend_strategy": "kNextPowerOfTwo",
                        "cudnn_conv_algo_search": "HEURISTIC",
                    },
                ),
            )
        with tempfile.TemporaryDirectory(prefix="cctv-face-profile-") as temporary:
            for name, side, mean, std in (
                ("det_10g.onnx", 320, 127.5, 128.0),
                ("1k3d68.onnx", 192, 0.0, 1.0),
                ("w600k_r50.onnx", 112, 127.5, 127.5),
            ):
                options = ort.SessionOptions()
                options.intra_op_num_threads = 2
                # Fixed GPU arena stays bounded across repeated runs and worker threads.
                # The pinned ORT/model combination exhausted the arena with patterns enabled.
                options.enable_mem_pattern = False
                options.enable_profiling = True
                options.profile_file_prefix = str(Path(temporary) / name)
                source = str(directory / name)
                if name == "det_10g.onnx":
                    # Release input is dynamic, but output metadata contains 640px counts.
                    # Correct only shape annotations in memory for our fixed 320px input.
                    graph = onnx.load(source)
                    for output in graph.graph.output:
                        output.type.tensor_type.shape.dim[0].dim_param = "anchors"
                    source = graph.SerializeToString()
                session = ort.InferenceSession(source, options, providers=providers)
                session.disable_fallback()
                cuda = "CUDAExecutionProvider" in session.get_providers()
                if self.device == "cuda" and not cuda:
                    if not settings.allow_cpu_fallback:
                        raise RuntimeError("Face CUDA session unavailable")
                    self.device = "cpu"
                    providers = ["CPUExecutionProvider"]
                blob = cv2.dnn.blobFromImage(
                    np.zeros((side, side, 3), dtype=np.uint8),
                    1 / std,
                    (side, side),
                    (mean,) * 3,
                    swapRB=True,
                )
                outputs = session.run(None, {session.get_inputs()[0].name: blob})
                expected = {"det_10g.onnx": 9, "1k3d68.onnx": 1, "w600k_r50.onnx": 1}[name]
                if len(outputs) != expected:
                    raise ValueError("Unexpected face model outputs")
                events = json.loads(Path(session.end_profiling()).read_text())
                nodes = [event for event in events if event.get("cat") == "Node"]
                cuda_nodes = [
                    e for e in nodes if e.get("args", {}).get("provider") == "CUDAExecutionProvider"
                ]
                cuda_conv = [
                    e for e in cuda_nodes if "Conv" in e.get("args", {}).get("op_name", "")
                ]
                if self.device == "cuda" and not cuda_conv:
                    raise RuntimeError("Face model did not execute CUDA convolutions")
                evidence[name] = {
                    "sha256": MODEL_HASHES[name],
                    "providers": session.get_providers(),
                    "cuda_node_count": len(cuda_nodes),
                    "cuda_conv_count": len(cuda_conv),
                    "warmup_input": [1, 3, side, side],
                    "memory_pattern": False,
                    "cuda_options": {
                        key: value
                        for key, value in session.get_provider_options()
                        .get("CUDAExecutionProvider", {})
                        .items()
                        if key
                        in {
                            "gpu_mem_limit",
                            "arena_extend_strategy",
                            "use_ep_level_unified_stream",
                            "cudnn_conv_use_max_workspace",
                            "cudnn_conv_algo_search",
                        }
                    },
                }
                self.sessions[name] = session
        devices = {
            name: "cuda" if item["cuda_conv_count"] else "cpu" for name, item in evidence.items()
        }
        actual = set(devices.values())
        self.device = next(iter(actual)) if len(actual) == 1 else "mixed"
        self.info = {
            "actual_device_per_model": devices,
            "status": "passed"
            if self.device == "cuda"
            else "cpu_fallback"
            if settings.requested_device == "cuda"
            else "cpu",
            "actual_device": self.device,
            "model_version": MODEL_VERSION,
            "embedding_dimension": 512,
            "embedding_normalization": "L2",
            "detector_input": [320, 320],
            "alignment_size": [112, 112],
            "models": evidence,
            "quality_calibrated": False,
        }

    def run(self, name, image, side, mean, std):
        blob = cv2.dnn.blobFromImage(image, 1 / std, (side, side), (mean,) * 3, swapRB=True)
        session = self.sessions[name]
        return session.run(None, {session.get_inputs()[0].name: blob})

    def detect(self, image):
        height, width = image.shape[:2]
        scale = min(320 / width, 320 / height)
        rw, rh = max(1, round(width * scale)), max(1, round(height * scale))
        canvas = np.zeros((320, 320, 3), dtype=np.uint8)
        canvas[:rh, :rw] = cv2.resize(image, (rw, rh))
        outputs = self.run("det_10g.onnx", canvas, 320, 127.5, 128.0)
        all_boxes, all_points, all_scores = [], [], []
        for index, stride in enumerate((8, 16, 32)):
            scores = outputs[index].reshape(-1)
            distances = outputs[index + 3].reshape(-1, 4) * stride
            offsets = outputs[index + 6].reshape(-1, 5, 2) * stride
            coordinates = np.mgrid[: 320 // stride, : 320 // stride][::-1].transpose(1, 2, 0)
            anchors = np.repeat(coordinates.reshape(-1, 2) * stride, 2, axis=0)
            keep = np.flatnonzero(scores >= self.settings.face_detection_threshold)
            if not len(keep):
                continue
            centers, distances = anchors[keep], distances[keep]
            boxes = np.column_stack((centers - distances[:, :2], centers + distances[:, 2:]))
            # Undo rounded resize separately for each axis.
            factor = np.array([width / rw, height / rh])
            all_boxes.append(boxes * np.tile(factor, 2))
            all_points.append((anchors[keep, None, :] + offsets[keep]) * factor)
            all_scores.append(scores[keep])
        if not all_boxes:
            return []
        boxes, points, scores = (
            np.concatenate(all_boxes),
            np.concatenate(all_points),
            np.concatenate(all_scores),
        )
        order, selected = scores.argsort()[::-1], []
        while len(order) and len(selected) < 10:
            current, rest = int(order[0]), order[1:]
            selected.append(current)
            intersection = np.maximum(
                0,
                np.minimum(boxes[current, 2:], boxes[rest, 2:])
                - np.maximum(boxes[current, :2], boxes[rest, :2]),
            )
            area = np.prod(np.maximum(0, boxes[:, 2:] - boxes[:, :2]), axis=1)
            overlap = np.prod(intersection, axis=1)
            iou = overlap / np.maximum(area[current] + area[rest] - overlap, 1e-8)
            order = rest[iou <= 0.4]
        return [
            {"bbox": boxes[i], "landmarks": points[i], "confidence": float(scores[i])}
            for i in selected
        ]

    def pose(self, image, bbox):
        x1, y1, x2, y2 = bbox
        scale = 192 / (max(x2 - x1, y2 - y1) * 1.5)
        center = np.array([(x1 + x2) / 2, (y1 + y2) / 2])
        matrix = np.column_stack((np.eye(2) * scale, 96 - center * scale)).astype(np.float32)
        crop = cv2.warpAffine(image, matrix, (192, 192), borderValue=0)
        # Pinned 1k3d68 contains bn_data normalization in the graph.
        landmarks = self.run("1k3d68.onnx", crop, 192, 0.0, 1.0)[0].reshape(-1, 3)[-68:].copy()
        landmarks[:, :2] = (landmarks[:, :2] + 1) * 96
        landmarks[:, 2] *= 96
        inverse = cv2.invertAffineTransform(matrix)
        landmarks[:, :2] = landmarks[:, :2] @ inverse[:, :2].T + inverse[:, 2]
        landmarks[:, 2] /= scale
        transform = np.linalg.lstsq(
            np.column_stack((self.mean_shape, np.ones(68))), landmarks, rcond=None
        )[0].T
        first, second = transform[:2, :3]
        first /= max(float(np.linalg.norm(first)), 1e-8)
        second /= max(float(np.linalg.norm(second)), 1e-8)
        rotation = np.array([first, second, np.cross(first, second)])
        sy = math.hypot(rotation[0, 0], rotation[1, 0])
        pitch = (
            math.atan2(rotation[2, 1], rotation[2, 2])
            if sy >= 1e-6
            else math.atan2(-rotation[1, 2], rotation[1, 1])
        )
        yaw = math.atan2(-rotation[2, 0], sy)
        roll = math.atan2(rotation[1, 0], rotation[0, 0]) if sy >= 1e-6 else 0
        result = dict(
            zip(("pitch", "yaw", "roll"), map(math.degrees, (pitch, yaw, roll)), strict=True)
        )
        if not all(math.isfinite(value) for value in result.values()):
            raise ValueError("Invalid pose prediction")
        return result

    def embed(self, aligned):
        return normalized_embedding(self.run("w600k_r50.onnx", aligned, 112, 127.5, 127.5)[0])
