"""Run the private GPU worker natively, with native decoder diagnostics suppressed."""

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ["OPENCV_LOG_LEVEL"] = "SILENT"
os.environ["OPENCV_FFMPEG_LOGLEVEL"] = "-8"
os.environ["YOLO_CONFIG_DIR"] = str(ROOT / "data/ultralytics")
os.environ["YOLO_AUTOINSTALL"] = "false"
os.environ["YOLO_OFFLINE"] = "true"


def main():
    import logging

    import uvicorn
    from app.core.config import Settings
    from app.core.logging import configure_logging

    settings = Settings()
    configure_logging(settings.log_dir, "worker.log")
    # FFmpeg's native stderr may contain source URLs. Application failures are
    # recorded separately with sanitized codes in the rotating Python file log.
    with open(os.devnull, "w") as sink:
        os.dup2(sink.fileno(), 2)
    from app.worker.api import create_worker

    try:
        uvicorn.run(
            create_worker(settings),
            host="127.0.0.1",
            port=urlsplit(settings.worker_url).port,
            workers=1,
            access_log=False,
            log_level="error",
        )
    except Exception as exc:
        logging.getLogger("cctv.worker").error("Worker startup failed; type=%s", type(exc).__name__)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
