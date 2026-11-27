from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CaseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    case_id: str | None = Field(default=None, min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
    title: str | None = Field(default=None, min_length=2, max_length=160)
    description: str = Field(min_length=3, max_length=4000)
    clue_notes: str | None = Field(default=None, max_length=1000)
    incident_time_text: str | None = Field(default=None, max_length=120)
    authorized: bool
    initial_zone: str = Field(default="upload-batch", min_length=1, max_length=80)
    neighbors: dict[str, list[str]] | None = None
    idempotency_key: str | None = Field(default=None, min_length=4, max_length=128)

    @model_validator(mode="after")
    def validate_jurisdictions(self):
        if not self.authorized:
            raise ValueError("Confirm that all footage for this investigation is authorized.")
        if self.neighbors is None:
            self.neighbors = {self.initial_zone: []}
        if not 1 <= len(self.neighbors) <= 6:
            raise ValueError("Define between one and six jurisdictions.")
        if self.initial_zone not in self.neighbors:
            raise ValueError("Initial jurisdiction must appear in the jurisdiction map.")
        for zone, adjacent in self.neighbors.items():
            if not zone.strip() or len(zone) > 80:
                raise ValueError("Jurisdiction names must contain 1 to 80 characters.")
            if len(adjacent) != len(set(adjacent)) or any(x not in self.neighbors or x == zone for x in adjacent):
                raise ValueError("Neighbors must be distinct, defined jurisdictions other than the jurisdiction itself.")
        return self


class CaseUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str | None = Field(default=None, min_length=2, max_length=160)
    description: str | None = Field(default=None, min_length=3, max_length=4000)
    clue_notes: str | None = Field(default=None, max_length=1000)
    incident_time_text: str | None = Field(default=None, max_length=120)


class CameraCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    camera_id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    jurisdiction: str = Field(min_length=1, max_length=80)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    source_type: str = Field(pattern="^(ip_camera|police_archive|dvr|upload)$")
    source_uri: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def validate_coordinates(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Provide both latitude and longitude.")
        return self


class RecordingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    case_id: str = Field(min_length=1, max_length=80)
    camera_id: str = Field(min_length=1, max_length=80)
    source_type: str = Field(pattern="^(ip_camera|police_archive|dvr)$")
    source_uri: str = Field(min_length=1, max_length=2048)
    recorded_at: datetime | None = None


class InvestigationStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str = Field(pattern="^(open|searching|review|closed)$")


class NVRReplayCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(min_length=1, max_length=80)
    recording_token: str = Field(min_length=1, max_length=256)
    recorded_at: datetime


class VisualTargetClue(BaseModel):
    """A visible target described for human-reviewed video search."""

    model_config = ConfigDict(extra="forbid")

    target_type: str = Field(min_length=2, max_length=40)
    color: str | None = None
    attributes: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("target_type", "color", mode="before")
    @classmethod
    def normalize_target_text(cls, value: Any) -> Any:
        if isinstance(value, str) and value.strip().casefold() in {"", "null", "none", "n/a", "unknown"}:
            return None
        return value.strip() if isinstance(value, str) else value

    @field_validator("attributes", mode="before")
    @classmethod
    def normalize_attributes(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                continue
            text = item.strip()
            if not text or text.casefold() in {"null", "none", "n/a", "unknown", "clothing_color/type"}:
                continue
            if text.casefold() not in seen:
                seen.add(text.casefold())
                cleaned.append(text)
        return cleaned


class VehicleClue(BaseModel):
    """Legacy-compatible clue envelope, now supporting people and objects too."""
    model_config = ConfigDict(extra="forbid")

    vehicle_color: str | None = None
    vehicle_type: str | None = None
    distinctive_vehicle_features: list[str] = Field(default_factory=list, max_length=12)
    visual_targets: list[VisualTargetClue] = Field(default_factory=list, max_length=20)
    unsupported_requests: list[str] = Field(default_factory=list, max_length=20)
    event_time_text: str | None = None
    origin_zone: str | None = None
    source_text: str
    parser: str

    @field_validator("vehicle_color", "vehicle_type", "event_time_text", "origin_zone", mode="before")
    @classmethod
    def normalize_null_strings(cls, value: Any) -> Any:
        if isinstance(value, str) and value.strip().casefold() in {"", "null", "none", "n/a", "unknown"}:
            return None
        return value.strip() if isinstance(value, str) else value

    @field_validator("distinctive_vehicle_features", "unsupported_requests", mode="before")
    @classmethod
    def normalize_feature_lists(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                continue
            text = item.strip()
            if not text or text.casefold() in {"null", "none", "n/a", "unknown", "clothing_color/type"}:
                continue
            if text.casefold() not in seen:
                seen.add(text.casefold())
                cleaned.append(text)
        return cleaned


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


class AnalyzeZonesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1, max_length=80)
    items: list[dict[str, Any]] = Field(min_length=1, max_length=100)


class ResolveWorkItemsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1, max_length=80)
    items: list[dict[str, Any]] = Field(min_length=1, max_length=1000)


class ReviewDecision(BaseModel):
    decision: str = Field(pattern="^(confirmed_candidate|not_relevant|needs_more_review)$")
    reviewer: str | None = Field(default=None, max_length=120)
    note: str = Field(default="", max_length=2000)

