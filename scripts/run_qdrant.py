"""Run Qdrant as a native process using project-local data and configuration."""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.config import ROOT, Settings


def main():
    settings = Settings()
    binary = ROOT / ".tools/qdrant/qdrant"
    if not binary.exists():
        raise SystemExit("Run scripts/install_qdrant.py first")
    metadata = json.loads((binary.parent / "release.json").read_text())
    if metadata["version"] != settings.qdrant_version:
        raise SystemExit("Installed Qdrant version does not match QDRANT_VERSION")
    os.chdir(ROOT)
    (ROOT / "data/qdrant").mkdir(parents=True, exist_ok=True)
    api_key = settings.qdrant_api_key.get_secret_value()
    if api_key:
        os.environ["QDRANT__SERVICE__API_KEY"] = api_key
    os.execv(str(binary), [str(binary), "--config-path", str(ROOT / "config/qdrant.yaml")])


if __name__ == "__main__":
    main()
