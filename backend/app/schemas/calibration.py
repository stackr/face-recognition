"""Ground truth format, independent of detector predictions or matching scores."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    source_id: str
    source_group: str  # Same capture/image and all derived variants share this group.
    split: Literal["smoke", "calibration", "evaluation"]
    media_path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    duration_seconds: float | None = Field(default=None, gt=0)


class Appearance(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    source_id: str
    subject_id: str
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    face_region: tuple[float, float, float, float] | None = None
    gallery_member: bool = False
    annotated_by: str


class Reference(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    reference_id: str
    source_id: str
    subject_id: str
    crop: tuple[float, float, float, float]


class Trial(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    first: str
    second: str
    same_subject: bool


class CalibrationDataset(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    schema_version: Literal[1] = 1
    purpose: Literal["pipeline_smoke", "threshold_evaluation"]
    provenance: str
    sources: list[Source]
    appearances: list[Appearance]
    references: list[Reference]
    trials: list[Trial]

    @model_validator(mode="after")
    def ground_truth_consistency(self):
        sources = {item.source_id: item for item in self.sources}
        references = {item.reference_id: item for item in self.references}
        if len(sources) != len(self.sources) or len(references) != len(self.references):
            raise ValueError("Duplicate source or reference ID")
        groups = {}
        for source in self.sources:
            if source.source_group in groups and groups[source.source_group] != source.split:
                raise ValueError("Capture group leaked across splits")
            groups[source.source_group] = source.split
            if self.purpose == "threshold_evaluation" and source.split == "smoke":
                raise ValueError("Smoke sources cannot evaluate thresholds")
            if source.media_path.startswith("/") or ".." in source.media_path.split("/"):
                raise ValueError("Use private project-relative media paths")
        for entry in [*self.appearances, *self.references]:
            if entry.source_id not in sources:
                raise ValueError("Unknown source ID")
            source = sources[entry.source_id]
            bbox = entry.face_region if isinstance(entry, Appearance) else entry.crop
            if bbox is not None:
                x1, y1, x2, y2 = bbox
                if not (0 <= x1 < x2 <= source.width and 0 <= y1 < y2 <= source.height):
                    raise ValueError("Ground truth region outside source")
            if isinstance(entry, Appearance) and (
                entry.end_seconds <= entry.start_seconds
                or source.duration_seconds is None
                or entry.end_seconds > source.duration_seconds
            ):
                raise ValueError("Invalid appearance interval")
        pairs_seen = set()
        for trial in self.trials:
            pair = frozenset((trial.first, trial.second))
            if self.purpose == "threshold_evaluation" and pair in pairs_seen:
                raise ValueError("Duplicate face pair trial")
            pairs_seen.add(pair)
            if (
                trial.first not in references
                or trial.second not in references
                or trial.first == trial.second
            ):
                raise ValueError("Invalid pair references")
            first_source = sources[references[trial.first].source_id]
            second_source = sources[references[trial.second].source_id]
            if (
                self.purpose == "threshold_evaluation"
                and first_source.source_group == second_source.source_group
            ):
                raise ValueError(
                    "Enrollment anchor and evaluation probe must use independent captures"
                )
            if (
                references[trial.first].subject_id == references[trial.second].subject_id
            ) != trial.same_subject:
                raise ValueError("Pair label conflicts with human subject labels")
        return self
