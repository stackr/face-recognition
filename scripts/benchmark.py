"""One-channel real-time-paced benchmark through the same public API as the UI."""

import argparse
import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from api_session import ROOT, api_session, checked


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, default=ROOT / "data/videos/people.mp4")
    parser.add_argument("--duration", type=float, default=30)
    parser.add_argument("--output", type=Path, default=ROOT / "data/reports/phase2-benchmark.json")
    args = parser.parse_args()
    if not 5 <= args.duration <= 300 or not args.video.is_file():
        raise SystemExit("Use an existing MP4 and a duration between 5 and 300 seconds")
    gpu_samples = []
    try:
        import pynvml

        pynvml.nvmlInit()
        gpu = pynvml.nvmlDeviceGetHandleByIndex(0)
    except Exception:
        gpu = None
    with api_session() as client:
        before = checked(client.get("/api/system/status"))
        if before["worker"]["status"] != "ok":
            raise RuntimeError("Worker unavailable")
        if any(
            run["state"] in {"opening", "running", "draining", "stopping"}
            for run in before["worker"]["cameras"]
        ):
            raise RuntimeError("Stop other cameras before a one-channel benchmark")
        camera = checked(
            client.post(
                "/api/cameras",
                json={"name": "benchmark_" + uuid.uuid4().hex[:12], "source_type": "mp4"},
            )
        )
        camera_id = camera["camera_id"]
        try:
            with args.video.open("rb") as stream:
                checked(
                    client.put(
                        f"/api/cameras/{camera_id}/video",
                        content=stream,
                        headers={
                            "Content-Type": "video/mp4",
                            "Content-Length": str(args.video.stat().st_size),
                        },
                    )
                )
            started = time.monotonic()
            checked(
                client.post(
                    f"/api/cameras/{camera_id}/start", json={"source_type": "mp4", "loop": True}
                )
            )
            first_result_seconds = None
            deadline = started + args.duration
            while time.monotonic() < deadline:
                status = checked(client.get(f"/api/cameras/{camera_id}/status"))
                if status["state"] == "error":
                    raise RuntimeError("Benchmark failed: " + status["error_code"])
                if status.get("processed_frames", 0) and first_result_seconds is None:
                    first_result_seconds = round(time.monotonic() - started, 3)
                if gpu is not None:
                    gpu_samples.append(
                        {
                            "utilization": pynvml.nvmlDeviceGetUtilizationRates(gpu).gpu,
                            "memory_mb": pynvml.nvmlDeviceGetMemoryInfo(gpu).used / 2**20,
                        }
                    )
                time.sleep(min(1, max(0, deadline - time.monotonic())))
            status = checked(client.get(f"/api/cameras/{camera_id}/status"))
            worker = checked(client.get("/api/system/status"))["worker"]
            if status["processed_frames"] < 2 or status["max_people"] < 1:
                raise RuntimeError(
                    "Benchmark requires processed frames with real person detections"
                )
            with client.stream("GET", f"/api/cameras/{camera_id}/preview") as preview:
                if preview.status_code != 200:
                    raise RuntimeError("Authenticated preview failed")
                chunk = next(preview.iter_bytes())
                if b"Content-Type: image/jpeg" not in chunk or b"\xff\xd8" not in chunk:
                    raise RuntimeError("Preview contains no annotated JPEG")
            status.pop("result", None)
            report = {
                "checked_at": datetime.now(UTC).isoformat(),
                "status": "passed",
                "channels": 1,
                "mode": "real_time_paced",
                "preview_viewers_during_measurement": 0,
                "preview_verified_at_end": True,
                "duration_seconds": args.duration,
                "first_result_seconds": first_result_seconds,
                "detector": worker["detector"],
                "worker_resources": worker["resources"],
                "camera": status,
                "face_analysis": "not_implemented_phase3",
                "latency_scope": "server decoded frame -> detection/tracking/JPEG; excludes browser and camera encoder",
                "gpu_utilization_mean_percent": round(
                    sum(x["utilization"] for x in gpu_samples) / len(gpu_samples), 2
                )
                if gpu_samples
                else None,
                "gpu_utilization_peak_percent": max(x["utilization"] for x in gpu_samples)
                if gpu_samples
                else None,
                "gpu_total_memory_peak_mb": round(max(x["memory_mb"] for x in gpu_samples), 1)
                if gpu_samples
                else None,
                "gpu_scope": "NVML whole GPU, includes other processes; utilization sampled once per second",
            }
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report, indent=2))
        finally:
            checked(client.post(f"/api/cameras/{camera_id}/stop"))
            checked(client.delete(f"/api/cameras/{camera_id}"))
    if gpu is not None:
        pynvml.nvmlShutdown()


if __name__ == "__main__":
    main()
