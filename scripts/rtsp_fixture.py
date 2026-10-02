"""Disposable, loopback-only RTSP fixture using the public face smoke MP4."""

import argparse
import hashlib
import json
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / "data/tools/mediamtx/mediamtx"
BINARY_SHA256 = "2b45b2999f22c8a1ecd376ce407b6337b68466fdeac54a8fdc448a79f818474f"


class RtspFixture:
    def __init__(self):
        if not BINARY.is_file() or hashlib.sha256(BINARY.read_bytes()).hexdigest() != BINARY_SHA256:
            raise RuntimeError("Run scripts/prepare_phase5.py to prepare the pinned RTSP test tool")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.url = f"rtsp://127.0.0.1:{self.port}/smoke"
        self.directory = tempfile.TemporaryDirectory(prefix="cctv-rtsp-")
        self.config = Path(self.directory.name) / "mediamtx.yml"
        self.config.write_text(
            f"logLevel: warn\nrtsp: true\nrtspAddress: 127.0.0.1:{self.port}\n"
            "readTimeout: 60s\nwriteTimeout: 60s\n"
            "rtspTransports: [tcp]\nrtmp: false\nhls: false\nwebrtc: false\nsrt: false\n"
            "api: false\nmetrics: false\nplayback: false\n"
            "paths:\n  smoke:\n    source: publisher\n"
        )
        self.server = self.publisher = None
        self.paused = False

    def up(self):
        if self.server is not None:
            self.resume()
            return
        try:
            self.server = subprocess.Popen(
                [str(BINARY), str(self.config)],
                cwd=self.directory.name,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if self.server.poll() is not None:
                    raise RuntimeError("Local RTSP fixture failed to start")
                try:
                    with socket.create_connection(("127.0.0.1", self.port), timeout=0.2):
                        break
                except OSError:
                    time.sleep(0.05)
            else:
                raise RuntimeError("Local RTSP fixture listener timed out")
            self.publisher = subprocess.Popen(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-re",
                    "-stream_loop",
                    "-1",
                    "-i",
                    str(ROOT / "data/videos/face-smoke.mp4"),
                    "-an",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    "-tune",
                    "zerolatency",
                    "-pix_fmt",
                    "yuv420p",
                    "-g",
                    "10",
                    "-f",
                    "rtsp",
                    "-rtsp_transport",
                    "tcp",
                    self.url,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.wait_published()
        except Exception:
            self.down()
            raise

    def wait_published(self):
        # A listening socket alone does not mean FFmpeg has published the path.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.publisher is None or self.publisher.poll() is not None:
                raise RuntimeError("Public smoke RTSP publisher failed")
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.3) as connection:
                    connection.sendall(
                        f"DESCRIBE {self.url} RTSP/1.0\r\nCSeq: 1\r\nAccept: application/sdp\r\n\r\n".encode()
                    )
                    if connection.recv(4096).startswith(b"RTSP/1.0 200"):
                        return
            except OSError:
                pass
            time.sleep(0.1)
        raise RuntimeError("Public smoke RTSP publication timed out")

    def pause(self):
        if self.server is None or self.server.poll() is not None:
            raise RuntimeError("Local RTSP fixture is not running")
        self.server.send_signal(signal.SIGSTOP)
        self.paused = True

    def resume(self):
        if self.paused and self.server is not None and self.server.poll() is None:
            self.server.send_signal(signal.SIGCONT)
        self.paused = False

    def down(self):
        self.resume()
        for process in (self.publisher, self.server):
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
        self.publisher = self.server = None

    def close(self):
        self.down()
        self.directory.cleanup()

    def __enter__(self):
        try:
            self.up()
        except Exception:
            self.close()
            raise
        return self

    def __exit__(self, *args):
        self.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stdio", action="store_true", required=True)
    parser.parse_args()

    def terminate(signum, frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, terminate)
    with RtspFixture() as fixture:
        print(json.dumps({"status": "ready", "url": fixture.url}), flush=True)
        for line in sys.stdin:
            command = json.loads(line)["command"]
            if command not in {"up", "down", "pause", "resume", "close"}:
                raise ValueError("Unsupported fixture command")
            getattr(fixture, command)()
            if command == "resume":
                fixture.wait_published()
            print(json.dumps({"status": "ok", "command": command}), flush=True)
            if command == "close":
                break


if __name__ == "__main__":
    main()
