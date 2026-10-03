"""Explicit download only; startup never fetches model weights."""

import hashlib
import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.core.config import Settings  # noqa: E402
from app.core.reid_data import (  # noqa: E402
    ARCHITECTURE_COMMIT,
    ARCHITECTURE_SHA256,
    MODEL_VERSION,
    WEIGHTS_FILENAME,
    WEIGHTS_REVISION,
    WEIGHTS_SHA256,
    WEIGHTS_URL,
)


def main():
    settings = Settings()
    path = settings.reid_model_path
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != WEIGHTS_SHA256:
        temp = path.with_suffix(".download")
        try:
            digest = hashlib.sha256()
            size = 0
            with httpx.stream("GET", WEIGHTS_URL, timeout=60, follow_redirects=True) as response:
                response.raise_for_status()
                with temp.open("wb") as stream:
                    os.chmod(temp, 0o600)
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > 12 * 2**20:
                            raise ValueError("Unexpected OSNet weight size")
                        digest.update(chunk)
                        stream.write(chunk)
            if digest.hexdigest() != WEIGHTS_SHA256:
                raise ValueError("OSNet author weight checksum mismatch")
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)
    manifest = {
        "model_version": MODEL_VERSION,
        "sha256": WEIGHTS_SHA256,
        "source": WEIGHTS_URL,
        "source_revision": WEIGHTS_REVISION,
        "original_filename": WEIGHTS_FILENAME,
        "training_dataset": "MSMT17 combineall",
        "license": "MIT per author model card; local non-commercial experiment",
        "architecture_commit": ARCHITECTURE_COMMIT,
        "architecture_sha256": ARCHITECTURE_SHA256,
    }
    path.with_suffix(".json").write_text(json.dumps(manifest, indent=2) + "\n")
    print({"status": "prepared", "model": MODEL_VERSION, "sha256": WEIGHTS_SHA256})


if __name__ == "__main__":
    main()
