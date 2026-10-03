"""Temporarily enable only the worker through a runtime systemd drop-in; restore it."""

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

from api_session import ROOT, api_session, checked


def main():
    with api_session() as client:
        before = checked(client.get("/api/system/status"))["worker"]
        if any(
            r["state"] in {"opening", "running", "reconnecting", "draining", "stopping"}
            for r in before["cameras"]
        ):
            raise RuntimeError("Re-ID browser verification requires an idle worker")
        controls = checked(client.get("/api/function-settings"))["values"]
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    directory = runtime / "systemd/user/cctv-worker.service.d"
    directory.mkdir(parents=True, exist_ok=True)
    override = directory / f"phase11-verify-{uuid.uuid4().hex}.conf"
    try:
        override.write_text("[Service]\nEnvironment=REID_ENABLED=true\n")
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run([sys.executable, str(ROOT / "scripts/restart_analysis.py")], check=True)
        subprocess.run(
            ["npx", "playwright", "test", "phase11.spec.ts"], cwd=ROOT / "frontend", check=True
        )
    finally:
        override.unlink(missing_ok=True)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run([sys.executable, str(ROOT / "scripts/restart_analysis.py")], check=True)
    with api_session() as client:
        after = checked(client.get("/api/system/status"))["worker"]
        current = checked(client.get("/api/function-settings"))["values"]
        if after["person_reid"]["status"] != before["person_reid"]["status"] or current != controls:
            raise RuntimeError("Verification configuration was not restored")
    report = {
        "status": "passed",
        "temporary_mode": "ready",
        "restored_mode": after["person_reid"]["status"],
        "settings_preserved": True,
    }
    path = ROOT / "data/reports/phase11-browser-restore.json"
    path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
