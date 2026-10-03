from pydantic import BaseModel, ConfigDict, Field

DEFAULT_MIN_FACE_SIZE = 8


class FaceTestOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, hide_input_in_errors=True)
    detection_threshold: float | None = Field(default=None, ge=0.1, le=0.99)
    min_face_size: int = Field(default=DEFAULT_MIN_FACE_SIZE, ge=8, le=512)
    match_threshold: float | None = Field(default=None, ge=-1, le=1)
