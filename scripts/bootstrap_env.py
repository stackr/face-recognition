"""Initialize ignored local configuration without echoing credentials."""

import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]


def main():
    target = ROOT / ".env"
    if target.exists():
        print(".env already exists; preserved")
        return
    example = (ROOT / ".env.example").read_text()
    database = ROOT / "DBCONFIG.md"
    values = {}
    if database.exists():
        for line in database.read_text().splitlines():
            name, separator, value = line.partition(":")
            if separator:
                values[name.strip().lower()] = value.strip()
    replacements = {
        "DB_NAME": values.get("db", "cctv_search_test"),
        "DB_USER": values.get("id", "change_me"),
        "DB_PASSWORD": values.get("pw", "change_me"),
        "SESSION_SECRET": secrets.token_urlsafe(48),
        "SERVICE_TOKEN": secrets.token_urlsafe(48),
        "RTSP_ENCRYPTION_KEY": Fernet.generate_key().decode(),
    }
    lines = []
    for line in example.splitlines():
        name = line.partition("=")[0]
        if name in replacements:
            # dotenv single-quote escaping protects special characters without shell evaluation.
            value = replacements[name].replace("\\", "\\\\").replace("'", "\\'")
            line = f"{name}='{value}'"
        lines.append(line)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write("\n".join(lines) + "\n")
    print("Generated .env with file mode 0600; credentials were not printed")


if __name__ == "__main__":
    main()
