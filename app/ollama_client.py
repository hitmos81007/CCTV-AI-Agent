from __future__ import annotations

import json
import os
import re

import httpx

from .schemas import VehicleClue


OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:latest")

_UNSUPPORTED_TERMS = (
    "facial recognition",
    "face recognition",
    "face match",
    "license plate",
    "number plate",
    "plate number",
    "plate ocr",
    "ocr",
    "biometric",
    "dna",
    "fingerprint",
    "cross-camera identity",
    "cross camera identity",
    "scar",
    "tattoo",
    "suspicious",
    "nervous",
)
_SUPPORTED_MACHINE_COLORS = {"black", "white", "gray", "grey", "red", "orange", "yellow", "green", "blue", "purple", "navy", "maroon", "crimson", "burgundy", "azure", "cyan"}
_UNSAFE_PATTERN = re.compile(r"(https?://|file://|[A-Za-z]:\\|/\w+/|\.\./|\$\(|`)", re.IGNORECASE)
_TIME_HINT_RE = re.compile(r"\b(\d{1,2}:\d{2}|\d{1,2}\s*(?:am|pm)|morning|afternoon|evening|night|midnight|noon|today|yesterday)\b", re.IGNORECASE)

CLUE_SCHEMA = {
    "type": "object",
    "properties": {
        "visual_targets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "target_type": {"type": "string", "enum": ["person", "car", "motorcycle", "bicycle", "bus", "truck", "other"]},
                    "color": {"type": ["string", "null"]},
                    "attributes": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["target_type", "color", "attributes"],
            },
        },
        "vehicle_color": {"type": ["string", "null"]},
        "vehicle_type": {"type": ["string", "null"]},
        "distinctive_vehicle_features": {"type": "array", "items": {"type": "string"}},
        "unsupported_requests": {"type": "array", "items": {"type": "string"}},
        "event_time_text": {"type": ["string", "null"]},
        "origin_zone": {"type": ["string", "null"]},
    },
    "required": [
        "visual_targets",
        "vehicle_color",
        "vehicle_type",
        "distinctive_vehicle_features",
        "unsupported_requests",
        "event_time_text",
        "origin_zone",
    ],
}


def _is_safe_text(text: str | None) -> bool:
    return bool(text) and not _UNSAFE_PATTERN.search(text)


def _sanitize_clue_payload(parsed: dict, description: str) -> dict:
    desc_lower = description.casefold()
    unsupported: list[str] = list(parsed.get("unsupported_requests") or [])

    # Reject invented origin_zone if not mentioned in the description
    origin_zone = parsed.get("origin_zone")
    if isinstance(origin_zone, str):
        if not _is_safe_text(origin_zone) or origin_zone.strip().casefold() not in desc_lower:
            origin_zone = None

    # Reject invented event_time_text if no time hint appears in description
    event_time_text = parsed.get("event_time_text")
    if isinstance(event_time_text, str):
        if not _is_safe_text(event_time_text) or not _TIME_HINT_RE.search(description):
            event_time_text = None

    # Clean distinctive_vehicle_features: never invent plate_absence unless explicitly stated
    cleaned_features: list[str] = []
    for feat in parsed.get("distinctive_vehicle_features") or []:
        if not isinstance(feat, str) or not _is_safe_text(feat):
            continue
        low = feat.strip().casefold()
        if low in {"null", "none", "n/a", "unknown"}:
            continue
        if "plate_absence" in low or ("plate" in low and "no " not in desc_lower and "without " not in desc_lower and "missing " not in desc_lower):
            if any(term in low for term in _UNSUPPORTED_TERMS):
                unsupported.append(feat.strip())
            continue
        if any(term in low for term in _UNSUPPORTED_TERMS):
            unsupported.append(feat.strip())
        else:
            cleaned_features.append(feat.strip())

    # Clean visual_targets and filter unsupported/invented attributes
    cleaned_targets = []
    for target in parsed.get("visual_targets") or []:
        if not isinstance(target, dict):
            continue
        t_type = str(target.get("target_type") or "").strip().casefold()
        if not t_type or t_type in {"null", "none"}:
            continue
        # Do not invent a generic "person" target if the prompt only mentioned unsupported facial/biometric tracking
        if t_type == "person" and not any(w in desc_lower for w in ("person", "man", "woman", "pedestrian", "someone", "individual", "rider", "cyclist", "jacket", "shirt", "coat", "backpack", "bag")):
            continue
        t_color = target.get("color")
        t_attrs = []
        if isinstance(t_color, str):
            c_low = t_color.strip().casefold()
            if not _is_safe_text(t_color) or c_low not in desc_lower:
                t_color = None
            elif c_low not in _SUPPORTED_MACHINE_COLORS:
                t_attrs.append(f"{t_color.strip()} appearance (ambiguous)")
                t_color = None
            else:
                t_color = t_color.strip()
        for attr in target.get("attributes") or []:
            if not isinstance(attr, str) or not _is_safe_text(attr):
                continue
            low = attr.strip().casefold()
            if low in {"null", "none", "n/a", "unknown", "clothing_color/type"}:
                continue
            if any(term in low for term in _UNSUPPORTED_TERMS):
                unsupported.append(attr.strip())
            else:
                t_attrs.append(attr.strip())
        cleaned_targets.append({"target_type": t_type, "color": t_color, "attributes": t_attrs})

    # Detect unsupported terms mentioned directly in description if missed by model
    for term in _UNSUPPORTED_TERMS:
        if term in desc_lower and not any(term in u.casefold() for u in unsupported):
            unsupported.append(term)

    vehicle_color = parsed.get("vehicle_color")
    if isinstance(vehicle_color, str) and (not _is_safe_text(vehicle_color) or vehicle_color.strip().casefold() not in desc_lower):
        vehicle_color = None
    vehicle_type = parsed.get("vehicle_type")
    if isinstance(vehicle_type, str) and not _is_safe_text(vehicle_type):
        vehicle_type = None

    # Filter unsupported_requests and preserve all detected unsupported requests
    verified_unsupported: list[str] = []
    for item in unsupported:
        if not isinstance(item, str) or not _is_safe_text(item):
            continue
        norm = item.strip().replace("_", " ")
        if norm and not any(norm.casefold() in u.casefold() for u in verified_unsupported):
            verified_unsupported.append(norm)

    return {
        "visual_targets": cleaned_targets,
        "vehicle_color": vehicle_color,
        "vehicle_type": vehicle_type,
        "distinctive_vehicle_features": cleaned_features,
        "unsupported_requests": verified_unsupported,
        "event_time_text": event_time_text,
        "origin_zone": origin_zone,
    }


async def parse_vehicle_clues(case_id: str, description: str, initial_zone: str) -> VehicleClue:
    system = (
        "Extract ONLY explicitly stated visible targets (person, car, motorcycle, bicycle, bus, truck, other), "
        "explicitly stated approximate colors, explicitly stated observable attributes, and explicitly stated time/zone from the case text. "
        "NEVER invent attributes, colors, recording times, plate_absence, or zones that are not explicitly in the text. "
        "NEVER copy placeholder words like 'null', 'none', or 'clothing_color/type' into arrays. "
        "Put unsupported requests (such as facial recognition, biometric identification, license plate OCR, plate numbers, or cross-camera tracking) "
        "into unsupported_requests, NOT into visual_targets or distinctive_vehicle_features. "
        "Use JSON null for missing scalar values and empty arrays when nothing is stated. Return only the requested JSON fields."
    )
    user = json.dumps({"case_id": case_id, "description": description, "initial_zone": initial_zone}, ensure_ascii=False)
    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "stream": False,
                "format": CLUE_SCHEMA,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "options": {"temperature": 0, "num_predict": 320},
            },
        )
        response.raise_for_status()
    content = response.json().get("message", {}).get("content", "")
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise ValueError("Ollama response must be a JSON object")
    sanitized = _sanitize_clue_payload(parsed, description)
    return VehicleClue(**sanitized, source_text=description, parser=f"ollama:{OLLAMA_MODEL}")
