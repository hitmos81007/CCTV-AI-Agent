from __future__ import annotations

import json
import os

import httpx

from .schemas import VehicleClue


OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:latest")

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
        "event_time_text": {"type": ["string", "null"]},
        "origin_zone": {"type": ["string", "null"]},
    },
    "required": ["visual_targets", "vehicle_color", "vehicle_type", "distinctive_vehicle_features", "event_time_text", "origin_zone"],
}


async def parse_vehicle_clues(case_id: str, description: str, initial_zone: str) -> VehicleClue:
    system = (
        "Extract explicitly stated visible targets and attributes, approximate event time, and zone from the supplied case text. "
        "Targets may be people, cars, motorcycles, bicycles, buses, trucks, or other visible objects. "
        "For people, use only non-face appearance such as clothing color/type or carried items; do not extract facial details, names, identity, or biometric traits. "
        "Keep scars, tattoos, suspected marks, plate absence, and other hard-to-verify details as plain attributes for human review, never as machine-verified facts. "
        "Do not infer guilt or unstated details. Use null for missing values. "
        "Return only the requested JSON fields."
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
                "options": {"temperature": 0},
            },
        )
        response.raise_for_status()
    content = response.json().get("message", {}).get("content", "")
    parsed = json.loads(content)
    return VehicleClue(**parsed, source_text=description, parser=f"ollama:{OLLAMA_MODEL}")

