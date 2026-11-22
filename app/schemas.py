from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CaseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=3, max_length=4000)
    initial_zone: str = Field(min_length=1, max_length=80)
    neighbors: dict[str, list[str]] = Field(default_factory=dict)


class VehicleClue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vehicle_color: str | None = None
    vehicle_type: str | None = None
    distinctive_vehicle_features: list[str] = Field(default_factory=list, max_length=12)
    event_time_text: str | None = None
    origin_zone: str | None = None
    source_text: str
    parser: str

    @field_validator("vehicle_color", "vehicle_type", "event_time_text", "origin_zone", mode="before")
    @classmethod
    def normalize_null_strings(cls, value: Any) -> Any:
        if isinstance(value, str) and value.strip().casefold() == "null":
            return None
        return value.strip() if isinstance(value, str) else value


class ClueParseRequest(BaseModel):
    case_id: str
    description: str = Field(min_length=3, max_length=4000)
    initial_zone: str = Field(min_length=1, max_length=80)


class AnalyzeZoneRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    case_id: str
    worker_id: str
    jurisdiction: str
    video_source_id: str
    description: str = ""
    clue: VehicleClue | None = None
    neighbors: list[str] = Field(default_factory=list)
    window_start: datetime | None = None
    window_end: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def flatten_n8n_request(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        nested = data.get("analysis_request")
        if isinstance(nested, dict) and not data.get("description"):
            data["description"] = nested.get("description", "")
        if data.get("video_source_id") is None:
            raise ValueError("video_source_id must refer to an uploaded local video")
        return data


class ResolveWorkItemsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1, max_length=80)
    items: list[dict[str, Any]] = Field(min_length=1, max_length=1000)


class ReviewDecision(BaseModel):
    decision: str = Field(pattern="^(confirmed_candidate|not_relevant|needs_more_review)$")
    reviewer: str = Field(min_length=1, max_length=120)
    note: str = Field(default="", max_length=2000)

