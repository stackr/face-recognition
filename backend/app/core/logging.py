import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging(directory: Path, filename: str = "application.log") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("cctv")
    if not logger.handlers:
        handler = RotatingFileHandler(
            directory / filename, maxBytes=5_000_000, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
