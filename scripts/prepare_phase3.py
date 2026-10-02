"""Download official non-commercial research models into private local storage."""

import hashlib
import json
import os
import pickle
import shutil
import zipfile
from pathlib import Path

import httpx
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_URL = "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip"
SHAPE_URL = "https://raw.githubusercontent.com/deepinsight/insightface/v0.7/python-package/insightface/data/objects/meanshape_68.pkl"
IMAGE_URL = "https://raw.githubusercontent.com/ultralytics/assets/main/im/zidane.jpg"
MODEL_FILES = ("det_10g.onnx", "w600k_r50.onnx", "1k3d68.onnx")
EXPECTED = {
    "zidane.jpg": "16d73869e3267a7d4ed00de8e860833bd1657c1b252e94c0c348277adc7b6edb",
    "buffalo_l.zip": "80ffe37d8a5940d59a7384c201a2a38d4741f2f3c51eef46ebb28218a7b0ca2f",
    "det_10g.onnx": "5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91",
    "w600k_r50.onnx": "4c06341c33c2ca1f86781dab0e829f88ad5b64be9fba56e56bc9ebdefc619e43",
    "1k3d68.onnx": "df5c06b8a0c12e422b2ed8947b8869faa4105387f199c477af038aa01f9a45cc",
    "meanshape_68.pkl": "39ffecf84ba73f0d0d7e49380833ba88713c9fcdec51df4f7ac45a48b8f4cc51",
}


def fetch(url, path, limit):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    if shutil.disk_usage(path.parent).free < limit + 500 * 2**20:
        raise RuntimeError("Insufficient space for model download")
    temporary = path.with_suffix(path.suffix + ".download")
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=60) as response:
            response.raise_for_status()
            size = 0
            with temporary.open("xb") as stream:
                os.chmod(temporary, 0o600)
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > limit:
                        raise RuntimeError("Download size limit reached")
                    stream.write(chunk)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(2**20), b""):
            result.update(chunk)
    return result.hexdigest()


def main():
    archive = ROOT / "data/models/buffalo_l.zip"
    directory = ROOT / "data/models/buffalo_l"
    directory.mkdir(parents=True, exist_ok=True)
    fetch(ARCHIVE_URL, archive, 400 * 2**20)
    if digest(archive) != EXPECTED[archive.name]:
        raise RuntimeError("Archive checksum mismatch")
    with zipfile.ZipFile(archive) as bundle:
        for name in MODEL_FILES:
            path = directory / name
            member = next(item for item in bundle.infolist() if Path(item.filename).name == name)
            if member.file_size > 200 * 2**20:
                raise RuntimeError("Model member exceeded size limit")
            if not path.exists():
                with bundle.open(member) as source, path.open("xb") as target:
                    shutil.copyfileobj(source, target)
                os.chmod(path, 0o600)
    shape = ROOT / "data/samples/meanshape_68.pkl"
    fetch(SHAPE_URL, shape, 128 * 1024)
    for path in [archive, shape, *(directory / name for name in MODEL_FILES)]:
        if path.name in EXPECTED and digest(path) != EXPECTED[path.name]:
            raise RuntimeError("Official artifact checksum mismatch: " + path.name)
    # Only the fixed official source is deserialized, never uploaded user files.
    mean = np.asarray(pickle.loads(shape.read_bytes()), dtype=np.float32)
    if mean.shape != (68, 3) or not np.isfinite(mean).all():
        raise RuntimeError("Invalid official mean face shape")
    np.save(directory / "meanshape_68.npy", mean, allow_pickle=False)
    os.chmod(directory / "meanshape_68.npy", 0o600)
    image = ROOT / "data/samples/zidane.jpg"
    fetch(IMAGE_URL, image, 5 * 2**20)
    if digest(image) != EXPECTED[image.name]:
        raise RuntimeError("Sample checksum mismatch")
    manifest = [
        {"path": str(path.relative_to(ROOT)), "bytes": path.stat().st_size, "sha256": digest(path)}
        for path in [
            archive,
            shape,
            image,
            *(directory / name for name in MODEL_FILES),
            directory / "meanshape_68.npy",
        ]
    ]
    report = {
        "model_package": "buffalo_l",
        "release": "v0.7",
        "source": ARCHIVE_URL,
        "pose_mean_source": SHAPE_URL,
        "sample_source": IMAGE_URL,
        "use": "non-commercial research only",
        "artifacts": manifest,
    }
    destination = ROOT / "data/reports/phase3-assets.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
