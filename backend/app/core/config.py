from pathlib import Path

from cryptography.fernet import Fernet
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", extra="ignore", hide_input_in_errors=True
    )

    db_host: str = "127.0.0.1"
    db_port: int = Field(default=3306, ge=1, le=65535)
    db_name: str = "cctv_search_test"
    db_user: str = ""
    db_password: SecretStr = SecretStr("")
    session_secret: SecretStr
    rtsp_encryption_key: SecretStr
    service_token: SecretStr
    cookie_secure: bool = False
    session_ttl_seconds: int = Field(default=28800, ge=60, le=604800)
    allowed_origins: list[str] = ["http://localhost:4200", "http://127.0.0.1:4200"]
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_api_key: SecretStr = SecretStr("")
    qdrant_version: str = "1.19.1"
    requested_device: str = "cuda"
    allow_cpu_fallback: bool = False
    gpu_report_path: Path = ROOT / "data/reports/gpu.json"
    log_dir: Path = ROOT / "logs"
    worker_url: str = "http://127.0.0.1:8001"
    yolo_model_path: Path = ROOT / "data/models/yolo11n.pt"
    video_dir: Path = ROOT / "data/videos"
    detection_fps: float = Field(default=5, ge=1, le=15)
    detection_confidence: float = Field(default=0.1, ge=0, le=1)
    track_low_threshold: float = Field(default=0.1, ge=0, le=1)
    track_high_threshold: float = Field(default=0.5, ge=0, le=1)
    new_track_threshold: float = Field(default=0.6, ge=0, le=1)
    track_lost_seconds: float = Field(default=3, ge=0.2, le=30)
    max_active_cameras: int = Field(default=4, ge=1, le=16)
    preview_fps: float = Field(default=5, ge=1, le=10)
    preview_viewers_per_camera: int = Field(default=4, ge=1, le=8)
    preview_viewers_total: int = Field(default=16, ge=1, le=32)
    video_upload_max_mb: int = Field(default=200, ge=1, le=1000)
    video_storage_max_mb: int = Field(default=1000, ge=200, le=10000)

    @model_validator(mode="after")
    def tracker_thresholds(self):
        if (
            not self.detection_confidence
            <= self.track_low_threshold
            < self.track_high_threshold
            <= self.new_track_threshold
        ):
            raise ValueError("Detector threshold must preserve low-score tracking detections")
        return self

    @field_validator("worker_url")
    @classmethod
    def loopback_worker(cls, value: str) -> str:
        from urllib.parse import urlsplit

        parsed = urlsplit(value)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.port is None
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("Worker must use a localhost HTTP URL")
        return value.rstrip("/")

    @field_validator("yolo_model_path", "video_dir", "log_dir", "gpu_report_path")
    @classmethod
    def project_path(cls, value: Path) -> Path:
        return value if value.is_absolute() else ROOT / value

    @field_validator("session_secret", "service_token")
    @classmethod
    def strong_secret(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32 or value.get_secret_value().startswith("generate_"):
            raise ValueError("A generated secret of at least 32 characters is required")
        return value

    @field_validator("rtsp_encryption_key")
    @classmethod
    def valid_encryption_key(cls, value: SecretStr) -> SecretStr:
        try:
            Fernet(value.get_secret_value().encode())
        except Exception:
            raise ValueError("A valid Fernet key is required") from None
        return value

    @field_validator("requested_device")
    @classmethod
    def valid_device(cls, value: str) -> str:
        if value not in {"cuda", "cpu"}:
            raise ValueError("Device must be cuda or cpu")
        return value

    @property
    def database_url(self) -> URL:
        return URL.create(
            "mysql+pymysql",
            username=self.db_user,
            password=self.db_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
            query={"charset": "utf8mb4"},
        )
