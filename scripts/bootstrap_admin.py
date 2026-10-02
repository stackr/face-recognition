"""Generate an initial administrator and save credentials only in a private ignored file."""

import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.config import ROOT, Settings
from app.core.security import password_hasher
from app.db.session import make_engine
from app.models import User
from sqlalchemy import select
from sqlalchemy.orm import Session


def main():
    path = ROOT / "data/local-admin.txt"
    engine = make_engine(Settings())
    try:
        with Session(engine) as db:
            if db.scalar(select(User).where(User.username == "admin")):
                print("Administrator exists; preserved")
                return
            if path.exists():
                raise SystemExit(
                    "Credentials file exists but account is absent; preserved for review"
                )
            password = secrets.token_urlsafe(24)
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as stream:
                stream.write(f"username: admin\npassword: {password}\n")
            db.add(
                User(username="admin", role="admin", password_hash=password_hasher.hash(password))
            )
            db.commit()
        print("Administrator created; credentials saved to data/local-admin.txt (0600)")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
