"""Create an account; obtain passwords from getpass or a protected environment variable."""

import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.config import Settings
from app.core.security import password_hasher
from app.db.session import make_engine
from app.models import User
from sqlalchemy import select
from sqlalchemy.orm import Session


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", required=True)
    parser.add_argument("--role", choices=["admin", "operator", "viewer"], default="admin")
    args = parser.parse_args()
    import re

    if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,64}", args.username):
        parser.error("Username must contain 1-64 letters, digits, _, . or -")
    password = os.environ.get("CCTV_INITIAL_PASSWORD") or getpass.getpass("Password: ")
    if len(password) < 12:
        parser.error("Password must contain at least 12 characters")
    engine = make_engine(Settings())
    try:
        with Session(engine) as db:
            if db.scalar(select(User).where(User.username == args.username)):
                parser.error("Account already exists; no changes made")
            db.add(
                User(
                    username=args.username,
                    role=args.role,
                    password_hash=password_hasher.hash(password),
                )
            )
            db.commit()
        print("Account created")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
