"""Download the official native binary and verify GitHub's asset SHA256."""

import argparse
import hashlib
import io
import json
import os
import platform
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "cctv-search-phase1"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="1.19.1")
    args = parser.parse_args()
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        parser.error("This installer supports Ubuntu Linux x86_64")
    import re

    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        parser.error("Expected a stable numeric version")
    directory = ROOT / ".tools/qdrant"
    metadata_path = directory / "release.json"
    if metadata_path.exists() and (directory / "qdrant").exists():
        metadata = json.loads(metadata_path.read_text())
        if metadata["version"] == args.version:
            print(f"Qdrant {args.version} already installed")
            return
        parser.error("A different version exists; upgrade it explicitly after stopping the service")
    release = json.loads(
        fetch(f"https://api.github.com/repos/qdrant/qdrant/releases/tags/v{args.version}")
    )
    name = "qdrant-x86_64-unknown-linux-gnu.tar.gz"
    asset = next((item for item in release["assets"] if item["name"] == name), None)
    if asset is None or not asset.get("digest", "").startswith("sha256:"):
        parser.error("Official binary or published SHA256 missing; installation stopped")
    archive = fetch(asset["browser_download_url"])
    digest = hashlib.sha256(archive).hexdigest()
    if digest != asset["digest"].removeprefix("sha256:"):
        parser.error("Archive checksum mismatch")
    directory.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
        member = next(
            item
            for item in bundle.getmembers()
            if Path(item.name).name == "qdrant" and item.isfile()
        )
        source = bundle.extractfile(member)
        if source is None:
            parser.error("Binary missing")
        binary = directory / "qdrant"
        binary.write_bytes(source.read())
        binary.chmod(0o755)
    metadata_path.write_text(
        json.dumps(
            {
                "version": args.version,
                "source": asset["browser_download_url"],
                "sha256": digest,
                "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            },
            indent=2,
        )
        + "\n"
    )
    os.chmod(metadata_path, 0o644)
    print(f"Qdrant {args.version} installed; official archive SHA256 verified")


if __name__ == "__main__":
    main()
