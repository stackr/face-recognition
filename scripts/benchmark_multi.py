"""Isolated 1/2/4-channel paced benchmark; never changes user sampling settings."""

import argparse
import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from api_session import ROOT, api_session, checked

sys.path.insert(0, str(ROOT / "backend"))


def summary(camera, seconds):
    camera = dict(camera)
    camera.pop("result", None)
    counts = camera.get("face_counts", {})
    camera["embedding_fps"] = round(counts.get("embeddings_created", 0) / seconds, 3)
    camera["queue_capacity"] = 1
    return camera


def gpu_sample(handle):
    if handle is None:
        return {}
    import pynvml

    processes = pynvml.nvmlDeviceGetComputeRunningProcesses(handle)
    return {
        "utilization_percent": pynvml.nvmlDeviceGetUtilizationRates(handle).gpu,
        "whole_gpu_memory_mb": pynvml.nvmlDeviceGetMemoryInfo(handle).used / 2**20,
        "compute_processes": [
            {"pid": p.pid, "gpu_memory_mb": p.usedGpuMemory / 2**20} for p in processes
        ],
    }


def paced(args, handle):
    results = []
    with api_session() as client:
        worker = checked(client.get("/api/system/status"))["worker"]
        active = {"opening", "running", "reconnecting", "draining", "stopping"}
        if any(r["state"] in active for r in worker["cameras"]):
            raise RuntimeError("Other cameras are active; benchmark requires an idle worker")
        settings_before = checked(client.get("/api/function-settings"))
        for channels in args.channels:
            ids = []
            try:
                for _ in range(channels):
                    row = checked(
                        client.post(
                            "/api/cameras",
                            json={
                                "name": "benchmark_" + uuid.uuid4().hex[:12],
                                "source_type": "mp4",
                            },
                        )
                    )
                    camera_id = row["camera_id"]
                    ids.append(camera_id)
                    with args.video.open("rb") as stream:
                        checked(
                            client.put(
                                f"/api/cameras/{camera_id}/video",
                                content=stream,
                                headers={"Content-Type": "video/mp4"},
                            )
                        )
                for camera_id in ids:
                    checked(
                        client.post(
                            f"/api/cameras/{camera_id}/start",
                            json={"source_type": "mp4", "loop": True},
                        )
                    )
                samples = []
                started = time.monotonic()
                while time.monotonic() - started < args.duration:
                    worker = checked(client.get("/api/system/status"))["worker"]
                    if any(
                        r["state"] in active and r["camera_id"] not in ids
                        for r in worker["cameras"]
                    ):
                        raise RuntimeError("Another camera started; measurement invalidated")
                    own = [r for r in worker["cameras"] if r["camera_id"] in ids]
                    if any(r["state"] == "error" for r in own):
                        raise RuntimeError("Benchmark camera failed")
                    samples.append(
                        gpu_sample(handle) | {"worker_rss_mb": worker["resources"].get("rss_mb")}
                    )
                    time.sleep(min(1, max(0, args.duration - (time.monotonic() - started))))
                worker = checked(client.get("/api/system/status"))["worker"]
                cameras = [
                    summary(r, r["elapsed_seconds"])
                    for r in worker["cameras"]
                    if r["camera_id"] in ids
                ]
                if len(cameras) != channels or any(
                    c["processed_frames"] < 2 or c["max_people"] < 1 for c in cameras
                ):
                    raise RuntimeError("No meaningful person workload")
                if args.require_embeddings and any(
                    c.get("face_counts", {}).get("embeddings_created", 0) == 0 for c in cameras
                ):
                    raise RuntimeError("Qualified embedding required on every channel")
                with client.stream("GET", f"/api/cameras/{ids[0]}/preview") as preview:
                    if preview.status_code != 200 or b"\xff\xd8" not in next(preview.iter_bytes()):
                        raise RuntimeError("Authenticated preview failed")
                item = {
                    "channels": channels,
                    "mode": "real_time_paced",
                    "duration_seconds": args.duration,
                    "cameras": cameras,
                    "aggregate_detection_fps": round(sum(c["detection_fps"] for c in cameras), 2),
                    "detector": worker["detector"],
                    "face_analysis": worker["face_analysis"],
                    "sampling": worker["sampling"],
                    "resources": worker["resources"],
                    "clips": worker.get("clips"),
                    "scheduler": worker.get("scheduler"),
                    "samples": samples,
                    "preview_viewers_during_measurement": 0,
                    "preview_verified_at_end": True,
                }
                results.append(item)
                print(
                    json.dumps(
                        {
                            "channels": channels,
                            "fps": [c["detection_fps"] for c in cameras],
                            "p95_ms": [c["latency_p95_ms"] for c in cameras],
                        }
                    ),
                    flush=True,
                )
            finally:
                for camera_id in ids:
                    try:
                        checked(client.post(f"/api/cameras/{camera_id}/stop"))
                    finally:
                        checked(client.delete(f"/api/cameras/{camera_id}"))
        if checked(client.get("/api/function-settings"))["values"] != settings_before["values"]:
            raise RuntimeError("Sampling settings changed during benchmark")
    return results


def offline(args, handle):
    import cv2
    import numpy as np
    from app.core.config import Settings
    from app.worker.detector import YoloPersonDetector
    from app.worker.faces import FaceAnalyzer, TrackFaces
    from app.worker.runtime import Frame
    from app.worker.tracker import CameraTracker

    with api_session() as client:
        worker = checked(client.get("/api/system/status"))["worker"]
        if any(
            r["state"] in {"opening", "running", "reconnecting", "draining", "stopping"}
            for r in worker["cameras"]
        ):
            raise RuntimeError("Offline benchmark requires an idle worker")
        saved = checked(client.get("/api/function-settings"))["values"]
    settings = Settings()
    for key, value in saved.items():
        setattr(settings, key, value)
    cv2.setNumThreads(settings.opencv_threads)
    detector = YoloPersonDetector(settings)
    face = None if args.disable_faces else FaceAnalyzer(settings)
    results = []
    for channels in args.channels:
        captures = [
            cv2.VideoCapture(
                str(args.video),
                cv2.CAP_FFMPEG,
                [cv2.CAP_PROP_N_THREADS, settings.capture_decode_threads],
            )
            for _ in range(channels)
        ]
        trackers = [CameraTracker(settings) for _ in captures]
        faces = [TrackFaces(settings) for _ in captures]
        counts, detection_ms, latency_ms, samples = [], [], [], []
        processed = max_people = 0
        started = time.monotonic()
        try:
            while time.monotonic() - started < args.duration:
                images = []
                for cap in captures:
                    ok, image = cap.read()
                    if not ok:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        ok, image = cap.read()
                    if not ok:
                        raise RuntimeError("Unreadable offline input")
                    images.append(image)
                captured = time.monotonic()
                boxes = detector.detect_batch(images)
                detection_ms.append((time.monotonic() - captured) * 1000 / channels)
                for i, image in enumerate(images):
                    frame = Frame(
                        image,
                        f"{i:032x}",
                        processed + i + 1,
                        datetime.now(UTC).isoformat(),
                        captured,
                    )
                    tracks = trackers[i].update(boxes[i], image, captured)
                    counts.append(
                        faces[i].process(face, frame, tracks, trackers[i].live_ids())
                        if face
                        else {}
                    )
                    max_people = max(max_people, len(tracks))
                    annotated = image.copy()
                    for track in tracks:
                        x1, y1, x2, y2 = map(int, track["bbox"])
                        cv2.rectangle(annotated, (x1, y1), (x2, y2), (80, 230, 120), 2)
                    if annotated.shape[1] > 1280:
                        annotated = cv2.resize(
                            annotated, (1280, round(annotated.shape[0] * 1280 / annotated.shape[1]))
                        )
                    cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    latency_ms.append((time.monotonic() - captured) * 1000)
                processed += channels
                if not samples or time.monotonic() - samples[-1]["sample_mono"] >= 1:
                    samples.append(gpu_sample(handle) | {"sample_mono": time.monotonic()})
            elapsed = time.monotonic() - started
            if (
                processed < channels * 2
                or max_people < 1
                or (
                    args.require_embeddings
                    and not any(c.get("embeddings_created", 0) for c in counts)
                )
            ):
                raise RuntimeError("Offline benchmark workload did not satisfy requirements")
            results.append(
                {
                    "channels": channels,
                    "mode": "offline_throughput",
                    "duration_seconds": elapsed,
                    "processed_frames": processed,
                    "aggregate_fps": processed / elapsed,
                    "per_camera_fps": processed / elapsed / channels,
                    "queue_capacity": 0,
                    "dropped_frames": 0,
                    "max_people": max_people,
                    "embedding_count": sum(c.get("embeddings_created", 0) for c in counts),
                    "detection_mean_amortized_ms": float(np.mean(detection_ms)),
                    "latency_mean_ms": float(np.mean(latency_ms)),
                    "latency_p95_ms": float(np.percentile(latency_ms, 95)),
                    "detector": detector.info,
                    "face_analysis": face.info if face else {"status": "disabled"},
                    "resources": detector.resources(),
                    "samples": samples,
                    "gallery": "disabled",
                    "event_and_clip_writes": False,
                }
            )
        finally:
            for cap in captures:
                cap.release()
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, default=ROOT / "data/videos/people.mp4")
    parser.add_argument("--duration", type=float, default=30)
    parser.add_argument("--channels", type=int, nargs="+", default=[1, 2, 4])
    parser.add_argument(
        "--mode", choices=["real_time_paced", "offline_throughput"], default="real_time_paced"
    )
    parser.add_argument("--require-embeddings", action="store_true")
    parser.add_argument(
        "--disable-faces", action="store_true", help="Offline detector-only comparison"
    )
    parser.add_argument("--output", type=Path, default=ROOT / "data/reports/benchmark-multi.json")
    args = parser.parse_args()
    if (
        not args.video.is_file()
        or not 5 <= args.duration <= 300
        or any(c not in (1, 2, 4) for c in args.channels)
    ):
        parser.error("Existing MP4, duration 5..300, channels 1/2/4 required")
    if args.disable_faces and (args.mode != "offline_throughput" or args.require_embeddings):
        parser.error("Face-disabled mode is offline only and cannot require embeddings")
    import pynvml

    try:
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
    except Exception:
        handle = None
    try:
        results = paced(args, handle) if args.mode == "real_time_paced" else offline(args, handle)
        report = {
            "status": "passed",
            "checked_at": datetime.now(UTC).isoformat(),
            "video": args.video.name,
            "accuracy_calibrated": False,
            "purpose": "performance_smoke",
            "mode": args.mode,
            "gpu_scope": "NVML whole GPU; other processes included; 1-second sampling",
            "latency_scope": "decoded frame -> server result/JPEG; excludes camera encoder/network/browser; offline excludes file decode",
            "results": results,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        os.chmod(args.output, 0o600)
    finally:
        if handle is not None:
            pynvml.nvmlShutdown()


if __name__ == "__main__":
    main()
