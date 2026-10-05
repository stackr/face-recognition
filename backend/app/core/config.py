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
    reid_enabled: bool = False
    reid_model_path: Path = ROOT / "data/models/osnet_x0_25_msmt17.pth"
    reid_interval_seconds: float = Field(default=2, ge=0.5, le=10)
    reid_rois_per_frame: int = Field(default=2, ge=1, le=4)
    reid_tracks_per_camera: int = Field(default=100, ge=1, le=200)
    face_model_dir: Path = ROOT / "data/models/buffalo_l"
    face_analysis_interval: float = Field(default=0.5, ge=0.2, le=10)
    face_rois_per_frame: int = Field(default=4, ge=1, le=16)
    face_tracks_per_camera: int = Field(default=100, ge=1, le=200)
    face_sample_count: int = Field(default=5, ge=2, le=8)
    face_sample_window_seconds: float = Field(default=3, ge=1, le=10)
    face_track_consistency_threshold: float = Field(default=0.3, ge=0, le=1)
    recognition_log_retention_days: int = Field(default=7, ge=1, le=30)
    recognition_log_max_records: int = Field(default=100000, ge=100, le=1000000)
    recognition_log_queue_size: int = Field(default=256, ge=16, le=1024)
    face_min_size: int = Field(default=80, ge=32, le=512)
    face_detection_threshold: float = Field(default=0.5, ge=0.1, le=0.99)
    person_detection_enabled: bool = True
    video_face_detection_threshold: float = Field(default=0.5, ge=0.1, le=0.99)
    video_face_min_size: int = Field(default=8, ge=8, le=512)
    face_quality_threshold: float = Field(default=0.7, ge=0, le=1)
    face_min_blur: float = Field(default=60, ge=1, le=1000)
    face_max_yaw: float = Field(default=40, ge=5, le=80)
    face_max_pitch: float = Field(default=30, ge=5, le=80)
    face_max_roll: float = Field(default=35, ge=5, le=80)
    face_test_dir: Path = ROOT / "data/face-tests"
    face_test_retention_hours: int = Field(default=24, ge=1, le=168)
    face_test_storage_max_mb: int = Field(default=1000, ge=200, le=10000)
    face_test_max_jobs_per_user: int = Field(default=10, ge=1, le=100)
    face_test_max_groups: int = Field(default=500, ge=1, le=2000)
    face_test_max_faces_per_frame: int = Field(default=100, ge=1, le=500)
    face_test_max_duration_seconds: int = Field(default=3600, ge=1, le=14400)
    video_dir: Path = ROOT / "data/videos"
    detection_fps: float = Field(default=5, ge=1, le=240)
    person_all_frames: bool = False
    face_detection_fps: float | None = Field(default=None, ge=0.1, le=240)
    face_all_frames: bool = False
    capture_decode_threads: int = Field(default=2, ge=1, le=8)
    opencv_threads: int = Field(default=2, ge=1, le=8)
    detector_batch_size: int = Field(default=4, ge=1, le=4)
    detector_batch_face_budget_ms: float = Field(default=100, ge=10, le=1000)
    yolo_fp16: bool = False
    detection_confidence: float = Field(default=0.1, ge=0, le=1)
    track_low_threshold: float = Field(default=0.1, ge=0, le=1)
    track_high_threshold: float = Field(default=0.5, ge=0, le=1)
    new_track_threshold: float = Field(default=0.6, ge=0, le=1)
    track_lost_seconds: float = Field(default=3, ge=0.2, le=30)
    max_active_cameras: int = Field(default=4, ge=1, le=16)
    rtsp_open_timeout_seconds: float = Field(default=5, ge=1, le=15)
    rtsp_read_timeout_seconds: float = Field(default=3, ge=1, le=10)
    rtsp_reconnect_initial_seconds: float = Field(default=1, ge=0.1, le=30)
    rtsp_reconnect_max_seconds: float = Field(default=30, ge=0.1, le=300)
    rtsp_reconnect_jitter: float = Field(default=0.2, ge=0, le=0.5)
    rtsp_reconnect_reset_seconds: float = Field(default=10, ge=1, le=300)
    preview_fps: float = Field(default=5, ge=1, le=10)
    preview_viewers_per_camera: int = Field(default=4, ge=1, le=8)
    preview_viewers_total: int = Field(default=16, ge=1, le=32)
    video_upload_max_mb: int = Field(default=200, ge=1, le=1000)
    video_storage_max_mb: int = Field(default=1000, ge=200, le=10000)
    reference_dir: Path = ROOT / "data/references"
    reference_upload_max_mb: int = Field(default=10, ge=1, le=20)
    reference_storage_max_mb: int = Field(default=200, ge=10, le=2000)
    reference_image_retention_days: int = Field(default=30, ge=1, le=365)
    reference_embedding_retention_days: int = Field(default=30, ge=1, le=365)
    reference_cleanup_interval_seconds: int = Field(default=30, ge=1, le=3600)
    max_target_persons: int = Field(default=100, ge=1, le=1000)
    reference_faces_per_person: int = Field(default=20, ge=1, le=50)
    face_search_provider: str = "auto"
    face_memory_max_references: int = Field(default=200, ge=1, le=2000)
    face_match_threshold: float = Field(default=0.75, ge=-1, le=1)
    face_collection: str = Field(default="face_embeddings", pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    api_url: str = "http://127.0.0.1:8000"
    event_dir: Path = ROOT / "data/events"
    event_cooldown: float = Field(default=30, ge=0, le=3600)
    event_retention_days: int = Field(default=30, ge=1, le=365)
    event_image_retention_days: int = Field(default=7, ge=1, le=365)
    event_storage_max_mb: int = Field(default=500, ge=10, le=10000)
    event_max_records: int = Field(default=50000, ge=100, le=1000000)
    event_queue_size: int = Field(default=32, ge=1, le=128)
    event_cleanup_interval_seconds: int = Field(default=30, ge=1, le=3600)
    event_ws_queue_size: int = Field(default=64, ge=1, le=256)
    event_ws_max_clients: int = Field(default=32, ge=1, le=128)
    event_ws_send_timeout_seconds: float = Field(default=5, ge=0.1, le=30)
    clip_enabled: bool = True
    clip_dir: Path = ROOT / "data/clips"
    clip_buffer_dir: Path = ROOT / "data/clip-buffer"
    video_buffer_before: float = Field(default=5, ge=0, le=30)
    video_buffer_after: float = Field(default=10, ge=0, le=60)
    video_buffer_max_bytes_per_camera: int = Field(default=32 * 2**20, ge=2**20)
    storage_max_bytes: int = Field(default=1024 * 2**20, ge=2**20)
    storage_min_free_bytes: int = Field(default=100 * 2**20, ge=0)
    clip_fps: int = Field(default=10, ge=1, le=15)
    clip_width: int = Field(default=1280, ge=160, le=1920)
    clip_retention_days: int = Field(default=7, ge=1, le=365)
    clip_max_pending: int = Field(default=32, ge=1, le=128)
    clip_encode_timeout_seconds: float = Field(default=45, ge=1, le=120)

    @model_validator(mode="after")
    def tracker_thresholds(self):
        if self.rtsp_reconnect_initial_seconds > self.rtsp_reconnect_max_seconds:
            raise ValueError("RTSP retry maximum must be at least the initial delay")
        # Raising the detector cutoff intentionally filters low-score associations.
        # Keep the tracker's own thresholds ordered independently of that cutoff.
        if not self.track_low_threshold < self.track_high_threshold <= self.new_track_threshold:
            raise ValueError("Tracker thresholds must be ordered")
        return self

    @field_validator("worker_url", "api_url")
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

    @field_validator(
        "yolo_model_path",
        "reid_model_path",
        "face_model_dir",
        "reference_dir",
        "event_dir",
        "clip_dir",
        "clip_buffer_dir",
        "video_dir",
        "face_test_dir",
        "log_dir",
        "gpu_report_path",
    )
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

    @field_validator("face_search_provider")
    @classmethod
    def search_provider(cls, value):
        if value not in {"auto", "memory", "qdrant"}:
            raise ValueError("Face search provider must be auto, memory or qdrant")
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
