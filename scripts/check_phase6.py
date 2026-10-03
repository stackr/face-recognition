"""Native Phase 6 smoke check; only owned cameras/persons and public sample images."""

import io
import json
import re
import sys
import time
import uuid
from datetime import UTC, datetime

import httpx
from api_session import ROOT, api_session, checked
from PIL import Image
from websockets.sync.client import connect

sys.path.insert(0, str(ROOT / "backend"))
from app.core.config import Settings
from app.core.security import COOKIE_NAME


class SmokeCheckFailure(AssertionError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(condition, code):
    if not condition:
        raise SmokeCheckFailure(code)


def cuda_devices(worker):
    detector = worker.get("detector", {}).get("actual_device")
    face = worker.get("face_analysis", {}).get("actual_device")
    require(
        isinstance(detector, str) and re.fullmatch(r"cuda(?::[0-9]+)?", detector) is not None,
        "detector_cuda_required",
    )
    require(face == "cuda", "face_cuda_required")
    return {"detector_device": detector, "face_device": face}


def owned_events(client, baseline, camera_id, person_id):
    rows, cursor = [], baseline
    for _ in range(100):
        page = checked(client.get("/api/events", params={"after_id": cursor, "limit": 100}))
        rows.extend(
            row
            for row in page["items"]
            if row["camera_id"] == camera_id and row["person_id"] == person_id
        )
        cursor = page["next_cursor"]
        if not page["has_more"]:
            return rows
    raise SmokeCheckFailure("event_recovery_page_limit_exceeded")


def candidate_message(socket, camera_id, person_id, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            item = json.loads(socket.recv(timeout=1))
        except TimeoutError:
            continue
        if (
            item.get("type") == "person_match"
            and item["camera_id"] == camera_id
            and item["person_id"] == person_id
        ):
            return item
    raise SmokeCheckFailure("websocket_candidate_timeout")


def main():
    settings = Settings()
    report = {
        "status": "unavailable",
        "checked_at": datetime.now(UTC).isoformat(),
        "purpose": "pipeline_smoke",
        "accuracy_calibrated": False,
        "checks": [],
    }
    camera_id = person_id = None
    cleanup_errors = []
    stage = "login"
    smoke_started = False
    try:
        with api_session() as client:
            try:
                stage = "system_status"
                system = checked(client.get("/api/system/status"))
                require(system["phase"] >= 6, "phase6_migration_and_restart_required")
                worker = system["worker"]
                stage = "cuda_devices"
                report.update(cuda_devices(worker))
                stage = "camera_capacity"
                active = {"opening", "running", "reconnecting", "draining", "stopping"}
                require(
                    sum(row["state"] in active for row in worker["cameras"])
                    < settings.max_active_cameras,
                    "no_free_smoke_camera_slot",
                )
                report["actual_device"] = "cuda"
                stage = "event_baseline"
                baseline = checked(client.get("/api/events", params={"latest": True}))[
                    "next_cursor"
                ]
                stage = "sample_assets"
                photo = ROOT / "data/calibration/person_b_reference.jpg"
                video = ROOT / "data/videos/face-smoke.mp4"
                require(photo.is_file(), "reference_photo_missing")
                require(video.is_file(), "smoke_video_missing")
                smoke_started = True
                stage = "reference_registration"
                suffix = uuid.uuid4().hex[:10]
                person_id = checked(
                    client.post("/api/persons", json={"name": "PHASE6-SMOKE-" + suffix})
                )["id"]
                reference = checked(
                    client.post(
                        f"/api/persons/{person_id}/faces",
                        content=photo.read_bytes(),
                        headers={"Content-Type": "image/jpeg"},
                    )
                )
                require(reference["state"] == "ready", "reference_not_ready")
                stage = "camera_registration"
                camera_id = checked(
                    client.post(
                        "/api/cameras",
                        json={"name": "PHASE6-SMOKE-" + suffix, "source_type": "mp4"},
                    )
                )["camera_id"]
                checked(
                    client.put(
                        f"/api/cameras/{camera_id}/video",
                        content=video.read_bytes(),
                        headers={"Content-Type": "video/mp4"},
                    )
                )
                cookie = {"Cookie": f"{COOKIE_NAME}={client.cookies.get(COOKIE_NAME)}"}
                origin = settings.allowed_origins[0]
                stage = "websocket_handshake"
                with connect(
                    "ws://127.0.0.1:8000/ws/events",
                    origin=origin,
                    additional_headers=cookie,
                    proxy=None,
                    open_timeout=5,
                    close_timeout=2,
                ) as socket:
                    require(
                        json.loads(socket.recv(timeout=5))["type"] == "ready",
                        "websocket_ready_required",
                    )
                    stage = "native_candidate"
                    checked(
                        client.post(
                            f"/api/cameras/{camera_id}/start",
                            json={"source_type": "mp4", "loop": False},
                        )
                    )
                    first = candidate_message(socket, camera_id, person_id)
                    require(first["status"] == "candidate", "candidate_status_required")
                    stage = "private_images"
                    for key in ("thumbnail_url", "frame_url"):
                        image = client.get(first[key])
                        require(image.status_code == 200, "event_image_unavailable")
                        dimensions = Image.open(io.BytesIO(image.content)).size
                        require(
                            dimensions == (112, 112)
                            if key == "thumbnail_url"
                            else max(dimensions) <= 1280,
                            "event_image_dimensions_invalid",
                        )
                    with httpx.Client(
                        base_url="http://127.0.0.1:8000", trust_env=False
                    ) as anonymous:
                        require(
                            anonymous.get(first["thumbnail_url"]).status_code == 401,
                            "anonymous_event_image_not_blocked",
                        )
                    stage = "event_dedup"
                    time.sleep(1)
                    rows = owned_events(client, baseline, camera_id, person_id)
                    keys = [
                        (
                            row["camera_id"],
                            row["stream_session_id"],
                            row["track_id"],
                            row["person_id"],
                        )
                        for row in rows
                    ]
                    require(len(keys) == len(set(keys)), "duplicate_event_key")
                    report["checks"].extend(
                        [
                            "native_cuda_candidate",
                            "authenticated_websocket",
                            "private_images",
                            "session_track_person_dedup",
                        ]
                    )
                # The client is offline while a review is saved, then recovers it by cursor.
                stage = "offline_review_recovery"
                rejected = checked(client.post(f"/api/events/{first['event_id']}/reject"))
                require(rejected["status"] == "rejected", "reject_not_saved")
                recovered = checked(
                    client.get("/api/events", params={"after_change_id": first["change_id"]})
                )
                require(
                    any(
                        row["event_id"] == first["event_id"] and row["status"] == "rejected"
                        for row in recovered["items"]
                    ),
                    "offline_review_not_recovered",
                )
                stage = "review_websocket_update"
                with connect(
                    "ws://127.0.0.1:8000/ws/events",
                    origin=origin,
                    additional_headers=cookie,
                    proxy=None,
                    open_timeout=5,
                    close_timeout=2,
                ) as socket:
                    require(
                        json.loads(socket.recv(timeout=5))["type"] == "ready",
                        "websocket_ready_required",
                    )
                    confirmed = checked(client.post(f"/api/events/{first['event_id']}/confirm"))
                    require(confirmed["status"] == "confirmed", "confirm_not_saved")
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        update = candidate_message(socket, camera_id, person_id, timeout=10)
                        if (
                            update["event_id"] == first["event_id"]
                            and update["status"] == "confirmed"
                        ):
                            break
                    else:
                        raise SmokeCheckFailure("review_websocket_update_timeout")
                    stage = "new_session_event"
                    checked(client.post(f"/api/cameras/{camera_id}/stop"))
                    restarted = checked(
                        client.post(
                            f"/api/cameras/{camera_id}/start",
                            json={"source_type": "mp4", "loop": False},
                        )
                    )
                    next_session = candidate_message(socket, camera_id, person_id)
                    require(
                        restarted["stream_session_id"]
                        == next_session["stream_session_id"]
                        != first["stream_session_id"],
                        "new_session_event_mismatch",
                    )
                    require(
                        next_session["event_id"] != first["event_id"],
                        "new_session_reused_event_id",
                    )
                report["checks"].extend(
                    [
                        "review_saved",
                        "offline_change_recovery",
                        "review_websocket_update",
                        "new_session_new_event",
                    ]
                )
                report["status"] = "passed"
            finally:
                if camera_id is not None:
                    try:
                        checked(client.delete(f"/api/cameras/{camera_id}"))
                    except Exception as exc:
                        cleanup_errors.append(type(exc).__name__)
                if person_id is not None:
                    try:
                        response = client.delete(f"/api/persons/{person_id}")
                        if response.status_code not in {202, 204}:
                            raise SmokeCheckFailure("owned_smoke_person_cleanup_failed")
                    except Exception as exc:
                        cleanup_errors.append(type(exc).__name__)
    except Exception as exc:
        report["status"] = "failed" if smoke_started else "unavailable"
        report["error_type"] = type(exc).__name__
        report["failed_stage"] = stage
        if isinstance(exc, SmokeCheckFailure):
            report["error_code"] = exc.code
    if cleanup_errors:
        report["status"], report["cleanup_errors"] = "failed", cleanup_errors
    output = ROOT / "data/reports/phase6-native.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
