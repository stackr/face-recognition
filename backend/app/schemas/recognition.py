from pydantic import BaseModel, ConfigDict, Field


def sample_window(settings, interval=None):
    # Slow user-selected sampling must still leave time to collect two faces.
    return max(
        settings.face_sample_window_seconds,
        (interval if interval is not None else settings.face_analysis_interval) * 2.5,
    )


class SamplingSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    detection_fps: float = Field(ge=1, le=15)
    face_analysis_interval: float = Field(ge=0.2, le=10)
    face_rois_per_frame: int = Field(ge=1, le=16)
    face_match_threshold: float = Field(default=0.75, ge=-1, le=1)
    detection_confidence: float = Field(default=0.1, ge=0, le=1)

    @classmethod
    def defaults(cls, settings):
        return cls(**{name: getattr(settings, name) for name in cls.model_fields})


class SamplingUpdate(SamplingSettings):
    face_match_threshold: float = Field(ge=-1, le=1)
    detection_confidence: float = Field(ge=0, le=1)
    revision: int = Field(ge=0)
