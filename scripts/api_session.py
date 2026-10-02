"""Local verification login; read the private credential file without printing it."""

from contextlib import contextmanager
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def api_session():
    fields = dict(
        line.split(": ", 1)
        for line in (ROOT / "data/local-admin.txt").read_text().splitlines()
        if ": " in line
    )
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=15, trust_env=False) as client:
        result = client.post(
            "/api/auth/login", json={"username": fields["username"], "password": fields["password"]}
        )
        if result.status_code != 200:
            raise RuntimeError("Verification login failed")
        client.headers["X-CSRF-Token"] = result.json()["csrf_token"]
        try:
            yield client
        finally:
            client.post("/api/auth/logout")


def checked(response):
    if response.status_code not in {200, 201, 204}:
        raise RuntimeError(f"Verification API failed with status {response.status_code}")
    return response.json() if response.content else None
