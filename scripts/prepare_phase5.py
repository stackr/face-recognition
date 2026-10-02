"""Download the pinned official MIT MediaMTX binary for localhost RTSP tests only."""

import hashlib
import io
import json
import os
import tarfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.21.1"
ARCHIVE_SHA256 = "653abc672a3e693f8d3b2717752492fdcfb8072291ec108d03d3dd857411b0ee"
URL = f"https://github.com/bluenviron/mediamtx/releases/download/v{VERSION}/mediamtx_v{VERSION}_linux_amd64.tar.gz"
DIRECTORY = ROOT / "data/tools/mediamtx"


def main():
    with httpx.Client(follow_redirects=True, timeout=60, trust_env=False) as client:
        response = client.get(URL)
        response.raise_for_status()
    if hashlib.sha256(response.content).hexdigest() != ARCHIVE_SHA256:
        raise RuntimeError("MediaMTX archive checksum mismatch")
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    hashes = {}
    with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:gz") as archive:
        for name in ("mediamtx", "LICENSE", "mediamtx.yml"):
            entry = archive.getmember(name)
            if not entry.isfile():
                raise RuntimeError("Unexpected MediaMTX archive entry")
            content = archive.extractfile(entry).read()
            path = DIRECTORY / name
            path.write_bytes(content)
            os.chmod(path, 0o700 if name == "mediamtx" else 0o600)
            hashes[name] = hashlib.sha256(content).hexdigest()
    manifest = {
        "version": VERSION,
        "source": URL,
        "archive_sha256": ARCHIVE_SHA256,
        "files": hashes,
        "purpose": "localhost RTSP smoke testing only",
    }
    (DIRECTORY / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
