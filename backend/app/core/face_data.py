"""Model identity and CPU-only vector validation shared by API and worker."""

import numpy as np

MODEL_HASHES = {
    "det_10g.onnx": "5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91",
    "w600k_r50.onnx": "4c06341c33c2ca1f86781dab0e829f88ad5b64be9fba56e56bc9ebdefc619e43",
    "1k3d68.onnx": "df5c06b8a0c12e422b2ed8947b8869faa4105387f199c477af038aa01f9a45cc",
    "meanshape_68.npy": "e32b77ecb2a39112eea88e727bd39db5319f5593590cecebcd3955d0b2d2257c",
}
MODEL_VERSION = "buffalo_l-v0.7-w600k_r50-" + MODEL_HASHES["w600k_r50.onnx"][:12]


def normalized_embedding(value):
    value = np.asarray(value, dtype=np.float32).reshape(-1)
    if value.shape != (512,) or not np.isfinite(value).all():
        raise ValueError("Invalid ArcFace embedding")
    norm = float(np.linalg.norm(value))
    if norm < 1e-8:
        raise ValueError("Zero ArcFace embedding")
    return value / norm
