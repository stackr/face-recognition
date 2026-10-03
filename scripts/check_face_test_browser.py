"""Use a temporary account to verify full deletion without touching user results."""

import json
import os
import secrets
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import cv2  # noqa: E402
import httpx  # noqa: E402
import numpy as np  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.core.face_test_data import write_private  # noqa: E402
from app.core.security import password_hasher  # noqa: E402
from app.db.session import make_engine  # noqa: E402
from app.models import User  # noqa: E402
from sqlalchemy import delete  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402


def prepare_videos(directory):
    source = cv2.imread(str(ROOT / "data/samples/zidane.jpg"))
    if source is None:
        raise RuntimeError("Run prepare_phase3.py first")
    paths = {}
    for name, frame, count in [
        ("faces", source, 40),
        ("blank", np.zeros((120, 160, 3), dtype=np.uint8), 20),
    ]:
        path = directory / f"{name}.mp4"
        writer = cv2.VideoWriter(
            str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (frame.shape[1], frame.shape[0])
        )
        if not writer.isOpened():
            raise RuntimeError("Video fixture creation failed")
        for _ in range(count):
            writer.write(frame)
        writer.release()
        path.chmod(0o600)
        paths[name] = str(path)
    return paths


def main():
    settings = Settings()
    engine = make_engine(settings)
    username = f"FACE-TEST-E2E-{uuid.uuid4().hex[:16]}"
    password = secrets.token_urlsafe(32)
    user_id = None
    (ROOT / "data").mkdir(exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="face-test-e2e-", dir=ROOT / "data") as temporary:
            directory = Path(temporary)
            videos = prepare_videos(directory)
            with Session(engine) as db:
                user = User(
                    username=username, password_hash=password_hasher.hash(password), role="viewer"
                )
                db.add(user)
                db.commit()
                user_id = user.id
            account = directory / "account.json"
            write_private(
                account,
                json.dumps({"username": username, "password": password, "videos": videos}).encode(),
            )
            environment = os.environ.copy()
            environment["FACE_TEST_E2E_ACCOUNT"] = str(account)
            try:
                subprocess.run(
                    ["npx", "playwright", "test", "face-test.spec.ts"],
                    cwd=ROOT / "frontend",
                    env=environment,
                    timeout=240,
                    check=True,
                )
            finally:
                with httpx.Client(base_url=settings.api_url, timeout=30, trust_env=False) as client:
                    login = client.post(
                        "/api/auth/login", json={"username": username, "password": password}
                    )
                    if login.status_code != 200:
                        raise RuntimeError("Temporary account cleanup login failed")
                    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
                    if client.delete("/api/face-tests").status_code != 200:
                        raise RuntimeError("Temporary video test cleanup failed")
                    client.post("/api/auth/logout")
    finally:
        if user_id is not None:
            with Session(engine) as db:
                db.execute(delete(User).where(User.id == user_id, User.username == username))
                db.commit()
        engine.dispose()

    print(
        json.dumps(
            {
                "status": "passed",
                "temporary_account_removed": True,
                "user_results_untouched": True,
            }
        )
    )


if __name__ == "__main__":
    main()
