from pydantic import BaseModel, ConfigDict, Field, model_validator


def face_interval(settings):
    if settings.face_all_frames:
        return 0
    fps = settings.face_detection_fps
    return 1 / fps if fps is not None else settings.face_analysis_interval


def sample_window(settings, interval=None):
    # Slow user-selected sampling must still leave time to collect two faces.
    return max(
        settings.face_sample_window_seconds,
        (interval if interval is not None else face_interval(settings)) * 2.5,
    )


class SamplingSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    detection_fps: float = Field(ge=1, le=240)
    person_all_frames: bool = Field(default=False, strict=True)
    face_detection_fps: float = Field(default=2, ge=0.1, le=240)
    face_all_frames: bool = Field(default=False, strict=True)
    face_analysis_interval: float = Field(ge=0.2, le=10)
    face_rois_per_frame: int = Field(ge=1, le=16)
    face_match_threshold: float = Field(default=0.75, ge=-1, le=1)
    detection_confidence: float = Field(default=0.1, ge=0, le=1)
    person_detection_enabled: bool = Field(default=True, strict=True)
    video_face_detection_threshold: float = Field(default=0.5, ge=0.1, le=0.99)
    video_face_min_size: int = Field(default=8, ge=8, le=512, strict=True)

    @model_validator(mode="before")
    @classmethod
    def legacy_frequency(cls, values):
        if isinstance(values, dict) and "face_detection_fps" not in values:
            values = dict(values)
            interval = values.get("face_analysis_interval", 0.5)
            if isinstance(interval, (float, int)) and interval > 0:
                values["face_detection_fps"] = 1 / interval
        return values

    @classmethod
    def defaults(cls, settings):
        values = {name: getattr(settings, name) for name in cls.model_fields}
        if values["face_detection_fps"] is None:
            values.pop("face_detection_fps")
        return cls(**values)


class SamplingUpdate(SamplingSettings):
    face_match_threshold: float = Field(ge=-1, le=1)
    detection_confidence: float = Field(ge=0, le=1)
    revision: int = Field(ge=0)
