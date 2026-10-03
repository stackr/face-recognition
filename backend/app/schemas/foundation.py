from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: SecretStr = Field(min_length=1, max_length=256)


class UserOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    role: str


class AuthOutput(BaseModel):
    user: UserOutput
    csrf_token: str


class CameraInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    source_type: Literal["rtsp", "mp4"] = "rtsp"
    rtsp_url: SecretStr = Field(default=SecretStr(""), max_length=2048)
    location: str = Field(default="", max_length=255)
    enabled: bool = True

    @model_validator(mode="before")
    @classmethod
    def source_fields(cls, value):
        # An inactive RTSP control can still contain a value after switching to MP4.
        # MP4 sources do not validate or retain those unrelated credentials.
        if isinstance(value, dict) and value.get("source_type") == "mp4":
            return {**value, "rtsp_url": ""}
        return value

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A camera name is required")
        return value.strip()

    @field_validator("rtsp_url")
    @classmethod
    def valid_rtsp(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value():
            return value
        try:
            parsed = urlsplit(value.get_secret_value())
            valid = parsed.scheme in {"rtsp", "rtsps"} and bool(parsed.hostname)
            _ = parsed.port
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("A valid RTSP or RTSPS URL is required")
        return value

    @model_validator(mode="after")
    def required_rtsp(self):
        if self.source_type == "rtsp" and not self.rtsp_url.get_secret_value():
            raise ValueError("RTSP URL is required")
        return self


class CameraOutput(BaseModel):
    camera_id: int
    name: str
    description: str
    rtsp_url: str  # Credentials and query parameters are always removed.
    source_type: str
    has_test_video: bool
    location: str
    enabled: bool
    can_view: bool
    can_operate: bool
    created_at: datetime
    updated_at: datetime
