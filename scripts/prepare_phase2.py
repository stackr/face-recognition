"""Fetch official YOLO weights and a small official sample for local experiments."""

import hashlib
import json
import os
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
ASSETS = {
    "data/models/yolo11n.pt": "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt",
    "data/samples/vtest.avi": "https://raw.githubusercontent.com/opencv/opencv/4.12.0/samples/data/vtest.avi",
}
EXPECTED = {
    "data/models/yolo11n.pt": "0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1",
    "data/samples/vtest.avi": "45cddc9490be69345cbdab64ca583be65987e864ca408038e648db99e10516cf",
}


def main():
    manifest = []
    for relative, url in ASSETS.items():
        path = ROOT / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            temporary = path.with_suffix(path.suffix + ".download")
            try:
                with httpx.stream("GET", url, follow_redirects=True, timeout=60) as response:
                    response.raise_for_status()
                    with temporary.open("wb") as stream:
                        os.chmod(temporary, 0o600)
                        size = 0
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            if size > 200 * 2**20:
                                raise RuntimeError("Sample download exceeded 200 MB")
                            stream.write(chunk)
                temporary.replace(path)
            except Exception:
                temporary.unlink(missing_ok=True)
                raise
        if hashlib.sha256(path.read_bytes()).hexdigest() != EXPECTED[relative]:
            raise RuntimeError("Official asset checksum differs: " + relative)
        manifest.append(
            {
                "path": relative,
                "source": url,
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    import cv2

    video = ROOT / "data/videos/people.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    if not video.exists():
        capture = cv2.VideoCapture(str(ROOT / "data/samples/vtest.avi"))
        fps = capture.get(cv2.CAP_PROP_FPS)
        width, height = (
            int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
            int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        )
        writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
        if not capture.isOpened() or not writer.isOpened():
            raise RuntimeError("Sample MP4 conversion failed")
        frames = 0
        try:
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                writer.write(frame)
                frames += 1
        finally:
            capture.release()
            writer.release()
        os.chmod(video, 0o600)
        if not frames:
            video.unlink(missing_ok=True)
            raise RuntimeError("Sample contains no frames")
    manifest.append(
        {
            "path": "data/videos/people.mp4",
            "source": "OpenCV 4.12.0 vtest.avi; re-encoded with OpenCV mp4v",
            "bytes": video.stat().st_size,
            "sha256": hashlib.sha256(video.read_bytes()).hexdigest(),
        }
    )
    directory = ROOT / "data/reports"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "phase2-assets.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
