"""Loopback IPC only. Never return worker URLs, source values or exception strings."""

import threading
from collections import Counter

import httpx
from fastapi import HTTPException


class WorkerClient:
    def __init__(self, settings, transport=None):
        self.client = httpx.Client(
            base_url=settings.worker_url,
            headers={"X-Service-Token": settings.service_token.get_secret_value()},
            timeout=10,
            transport=transport,
            trust_env=False,
        )

    def request(self, method, path, *, missing_ok=False, **kwargs):
        try:
            response = self.client.request(method, path, **kwargs)
        except httpx.HTTPError:
            raise HTTPException(503, "Analysis worker unavailable") from None
        if missing_ok and response.status_code == 404:
            return None
        if response.status_code != 200:
            if path == "/internal/references/analyze" and response.status_code == 422:
                detail = response.json().get("detail", {})
                if isinstance(detail, dict) and detail.get("code") in {
                    "invalid_image",
                    "image_dimensions_exceeded",
                    "no_face",
                    "multiple_faces",
                    "quality_rejected",
                }:
                    quality = detail.get("quality") or {}
                    raise HTTPException(
                        422,
                        {
                            "code": detail["code"],
                            "reasons": quality.get("reasons", []),
                            "quality": quality.get("quality"),
                        },
                    )
            code = response.status_code if response.status_code in {404, 409, 422, 429} else 503
            raise HTTPException(code, "Analysis command rejected")
        return response

    def status(self, camera_id):
        response = self.request("GET", f"/internal/cameras/{camera_id}", missing_ok=True)
        return (
            response.json()
            if response is not None
            else {"camera_id": camera_id, "state": "stopped"}
        )

    def stop(self, camera_id):
        response = self.request("POST", f"/internal/cameras/{camera_id}/stop", missing_ok=True)
        return (
            response.json()
            if response is not None
            else {"camera_id": camera_id, "state": "stopped"}
        )


class ViewerLimits:
    def __init__(self, settings):
        self.settings = settings
        self.lock = threading.Lock()
        self.counts = Counter()

    def acquire(self, camera_id):
        with self.lock:
            if (
                self.counts[camera_id] >= self.settings.preview_viewers_per_camera
                or sum(self.counts.values()) >= self.settings.preview_viewers_total
            ):
                raise HTTPException(429, "Preview viewer limit reached")
            self.counts[camera_id] += 1

    def release(self, camera_id):
        with self.lock:
            self.counts[camera_id] -= 1
            if self.counts[camera_id] <= 0:
                del self.counts[camera_id]
