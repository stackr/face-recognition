"""Install reviewed systemd user units for the current project and start them."""

import argparse
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", action="store_true")
    args = parser.parse_args()
    directory = Path.home() / ".config/systemd/user"
    templates = sorted((ROOT / "deploy/systemd").glob("*.service"))
    replacements = []
    for template in templates:
        target = directory / template.name
        text = template.read_text().replace("@PROJECT_ROOT@", str(ROOT))
        if target.exists() and target.read_text() != text:
            raise SystemExit(f"Existing {template.name} differs; preserved")
        replacements.append((target, text))
    directory.mkdir(parents=True, exist_ok=True)
    for target, text in replacements:
        target.write_text(text)
        os.chmod(target, 0o644)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    if args.start:
        names = [template.name for template in templates]
        subprocess.run(["systemctl", "--user", "enable", "--now", *names], check=True)
    print("Installed user services: " + ", ".join(template.name for template in templates))


if __name__ == "__main__":
    main()
