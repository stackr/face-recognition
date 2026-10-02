"""Verify native CUDA analysis across RTSP outage, recovery and stop; public samples only."""

import json
import sys
import time
import uuid

from api_session import ROOT, api_session, checked
from rtsp_fixture import RtspFixture

sys.path.insert(0, str(ROOT / "backend"))
from app.core.config import Settings
from app.services.worker_client import WorkerClient


def wait_for(client, camera_id, predicate, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = checked(client.get(f"/api/cameras/{camera_id}/status"))
        if predicate(state):
            return state
        time.sleep(0.1)
    raise RuntimeError("RTSP camera did not reach the expected state")


def candidate(state, person_id):
    return next(
        (
            track
            for track in (state.get("result") or {}).get("tracks", [])
            if any(
                match["person_id"] == person_id
                for match in track.get("face", {}).get("matches", [])
            )
        ),
        None,
    )


def thumbnail_path(camera_id, state, track):
    return f"/api/cameras/{camera_id}/faces/{track['track_id']}?stream_session_id={state['stream_session_id']}"


def main():
    settings = Settings()
    worker = WorkerClient(settings)
    report = {"status": "passed", "purpose": "pipeline_smoke", "accuracy_calibrated": False}
    camera_id = person_id = None
    with RtspFixture() as fixture, api_session() as client:
        client.timeout = 45
        try:
            system = checked(client.get("/api/system/status"))
            assert system["phase"] == 5
            detector_device = system["worker"]["detector"]["actual_device"]
            assert detector_device.startswith("cuda")
            assert system["worker"]["face_analysis"]["actual_device"] == "cuda"
            assert system["worker"]["face_analysis"]["status"] == "passed"
            report["checked_at"] = system["checked_at"]
            report["detector_device"] = detector_device
            report["face_device"] = "cuda"
            person_id = checked(
                client.post("/api/persons", json={"name": "PHASE5-CHECK-" + uuid.uuid4().hex[:8]})
            )["id"]
            checked(
                client.post(
                    f"/api/persons/{person_id}/faces",
                    content=ROOT.joinpath("data/calibration/person_b_reference.jpg").read_bytes(),
                    headers={"Content-Type": "image/jpeg"},
                )
            )
            camera_id = checked(
                client.post(
                    "/api/cameras",
                    json={
                        "name": "PHASE5-CHECK-" + uuid.uuid4().hex[:8],
                        "source_type": "rtsp",
                        "rtsp_url": fixture.url,
                    },
                )
            )["camera_id"]
            checked(client.post(f"/api/cameras/{camera_id}/start", json={"source_type": "rtsp"}))
            running = wait_for(
                client, camera_id, lambda s: s["state"] == "running" and candidate(s, person_id)
            )
            old_thumb = thumbnail_path(camera_id, running, candidate(running, person_id))
            assert client.get(old_thumb).status_code == 200
            report["initial_track_id"] = candidate(running, person_id)["track_id"]

            interrupted = time.monotonic()
            fixture.pause()  # TCP stays connected but no frames arrive: read timeout must fire.
            retry = wait_for(client, camera_id, lambda s: s["state"] == "reconnecting")
            report["read_stall_detected_seconds"] = round(time.monotonic() - interrupted, 2)
            assert retry["error_code"] == "source_read_failed"
            assert retry["stream_session_id"] != running["stream_session_id"]
            assert retry["result"] is None and retry["face_cache_tracks"] == 0
            assert retry["pending_frames"] == retry["session_processed_frames"] == 0
            assert client.get(old_thumb).status_code == 404
            frame = worker.client.get(f"/internal/cameras/{camera_id}/frame")
            assert frame.status_code == 204
            assert frame.headers["X-Camera-State"] == "reconnecting"
            fixture.resume()
            fixture.wait_published()
            restored = wait_for(
                client, camera_id, lambda s: s["state"] == "running" and candidate(s, person_id)
            )
            assert restored["stream_session_id"] != running["stream_session_id"]
            assert candidate(restored, person_id)["track_id"] == 1
            assert restored["error_code"] is None and restored["reconnects"] >= 1
            assert client.get(old_thumb).status_code == 404
            assert (
                client.get(
                    thumbnail_path(camera_id, restored, candidate(restored, person_id))
                ).status_code
                == 200
            )
            report["recovered_track_id"] = 1
            report["fresh_embeddings_after_recovery"] = (
                restored["face_counts"]["embeddings_created"]
                > running["face_counts"]["embeddings_created"]
            )
            assert report["fresh_embeddings_after_recovery"]
            assert restored["pending_frames"] <= 1 and restored["dropped_frames"] > 0

            fixture.down()
            waiting = wait_for(
                client,
                camera_id,
                lambda s: s["state"] == "reconnecting" and s["consecutive_failures"] >= 3,
            )
            assert 0 < waiting["next_retry_seconds"] <= settings.rtsp_reconnect_max_seconds
            report["failed_attempts_before_stop"] = waiting["consecutive_failures"]
            report["retry_wait_seconds"] = waiting["next_retry_seconds"]
            stop_start = time.monotonic()
            stopped = checked(client.post(f"/api/cameras/{camera_id}/stop"))
            report["stop_during_backoff_seconds"] = round(time.monotonic() - stop_start, 3)
            assert report["stop_during_backoff_seconds"] < 1 and stopped["state"] == "stopped"
            fixture.up()
            time.sleep(1.2)
            unchanged = checked(client.get(f"/api/cameras/{camera_id}/status"))
            assert (
                unchanged["state"] == "stopped"
                and unchanged["connection_attempts"] == stopped["connection_attempts"]
            )
            assert unchanged["result"] is None and unchanged["face_cache_tracks"] == 0
            checked(client.post(f"/api/cameras/{camera_id}/start", json={"source_type": "rtsp"}))
            restarted = wait_for(
                client, camera_id, lambda s: s["state"] == "running" and candidate(s, person_id)
            )
            assert restarted["stream_session_id"] != restored["stream_session_id"]
            assert client.get(old_thumb).status_code == 404
            report["capture_statistics_before_stop"] = {
                key: waiting[key]
                for key in (
                    "captured_frames",
                    "processed_frames",
                    "dropped_frames",
                    "pending_frames",
                    "latency_mean_ms",
                    "latency_p95_ms",
                    "reconnect_attempts",
                    "reconnects",
                )
            }
            report["checks"] = [
                "native_cuda",
                "rtsp_tcp_read_timeout",
                "automatic_recovery",
                "fresh_session_tracks_faces_candidates",
                "old_thumbnail_rejected",
                "latest_frame_bounded",
                "repeated_open_failure_backoff",
                "stop_interrupts_backoff",
                "stopped_camera_stays_stopped",
                "manual_restart",
            ]
        finally:
            try:
                if camera_id is not None:
                    checked(client.post(f"/api/cameras/{camera_id}/stop"))
                    checked(client.delete(f"/api/cameras/{camera_id}"))
            finally:
                if person_id is not None:
                    checked(client.delete(f"/api/persons/{person_id}"))
                worker.client.close()
    directory = ROOT / "data/reports"
    directory.mkdir(parents=True, exist_ok=True)
    directory.joinpath("phase5-native.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
