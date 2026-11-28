from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import logging
import mimetypes
import os
import re
import secrets
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import db
from .auth import AuthenticatedUser, owned_case_or_404, require_approved_officer
from .ingestion import validate_and_probe_uploaded_video
from .ollama_client import OLLAMA_BASE_URL, parse_vehicle_clues
from .schemas import AnalyzeZoneRequest, AnalyzeZonesRequest, CameraCreate, CaseCreate, CaseUpdate, ClueParseRequest, InvestigationStatusUpdate, NVRReplayCreate, RecordingCreate, ResolveWorkItemsRequest, ReviewDecision
from .verification_routes import router as verification_router
from .video_analysis import analyze_video


if os.getenv("CCTV_ENV", "").strip().lower() != "test":
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

app = FastAPI(title="CCTV Investigation Agent API", version="0.2.0")
logger = logging.getLogger("cctv.api")
app.include_router(verification_router)


@app.exception_handler(StarletteHTTPException)
async def structured_http_error(request: Request, exc: StarletteHTTPException):
    error_id = str(uuid.uuid4())
    logger.warning("request failed id=%s path=%s status=%s", error_id, request.url.path, exc.status_code)
    return JSONResponse(status_code=exc.status_code, headers=exc.headers,
                        content={"detail": exc.detail, "error": {"code": f"HTTP_{exc.status_code}", "id": error_id}})


@app.exception_handler(RequestValidationError)
async def structured_validation_error(request: Request, exc: RequestValidationError):
    error_id = str(uuid.uuid4())
    logger.warning("validation failed id=%s path=%s", error_id, request.url.path)
    errors = [{k: v for k, v in error.items() if k not in ("input", "ctx", "url")} for error in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors),
                        "error": {"code": "VALIDATION_ERROR", "id": error_id}})
cors_origins = [origin.strip() for origin in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if origin.strip()]
if cors_origins:
    if "*" in cors_origins:
        raise RuntimeError("CORS_ALLOWED_ORIGINS must list explicit frontend origins")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
    )
DEFAULT_MAX_UPLOAD_MB = 200
DEFAULT_MAX_CASE_VIDEOS = 10
SAFE_CASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
_ACTIVE_STAGING_GUARD = threading.Lock()
_ACTIVE_STAGING_PATHS: set[Path] = set()


def _case_upload_lock(case_id: str) -> threading.Lock:
    return db.case_lock(case_id)


def _reconcile_incomplete_staged_uploads(max_age_seconds: int = 3600) -> int:
    cases_root = (db.EVIDENCE_DIR.resolve(strict=False) / "cases").resolve(strict=False)
    if not cases_root.is_dir():
        return 0
    cutoff = time.time() - max(0, max_age_seconds)
    removed = 0
    for staging in cases_root.glob("*/.staging-*"):
        resolved = staging.resolve(strict=False)
        if not resolved.is_relative_to(cases_root) or not resolved.is_file():
            continue
        with _ACTIVE_STAGING_GUARD:
            if resolved in _ACTIVE_STAGING_PATHS:
                continue
        try:
            if max_age_seconds == 0 or resolved.stat().st_mtime <= cutoff:
                resolved.unlink(missing_ok=True)
                removed += 1
        except OSError:
            pass
    return removed


def _max_upload_bytes() -> int:
    raw = os.getenv("MAX_UPLOAD_MB", str(DEFAULT_MAX_UPLOAD_MB)).strip()
    mb = max(1, int(raw)) if raw else DEFAULT_MAX_UPLOAD_MB
    return mb * 1024 * 1024


def _max_case_videos() -> int:
    raw = os.getenv("MAX_CASE_VIDEOS", str(DEFAULT_MAX_CASE_VIDEOS)).strip()
    return max(1, min(50, int(raw))) if raw else DEFAULT_MAX_CASE_VIDEOS
FOLLOWUP_THRESHOLD = float(os.getenv("FOLLOWUP_MATCH_THRESHOLD", "0.70"))
VIDEO_WORKER_CONCURRENCY = max(1, min(4, int(os.getenv("VIDEO_WORKER_CONCURRENCY", "1"))))
ALLOWED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
ALLOWED_AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".webm", ".flac"}


@lru_cache(maxsize=1)
def _whisper_model():
    from faster_whisper import WhisperModel

    device = os.getenv("WHISPER_DEVICE", "cpu").strip().lower()
    compute_type = "float16" if device == "cuda" else "int8"
    return WhisperModel(os.getenv("WHISPER_MODEL", "base"), device=device, compute_type=compute_type)


@app.on_event("startup")
def startup() -> None:
    db.initialize()
    max_age = max(60, int(os.getenv("CCTV_STAGING_MAX_AGE_SECONDS", "3600")))
    _reconcile_incomplete_staged_uploads(max_age_seconds=max_age)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _matching_video_for_window(conn, case_id: str, zone: str, start: datetime, end: datetime):
    """Return the earliest registered local clip whose time range overlaps the requested window."""
    rows = conn.execute(
        "SELECT video_source_id,recorded_at,duration_seconds FROM videos WHERE case_id=? AND jurisdiction=? ORDER BY recorded_at",
        (case_id, zone),
    ).fetchall()
    for row in rows:
        clip_start = _parse_timestamp(row["recorded_at"])
        clip_end = clip_start + timedelta(seconds=float(row["duration_seconds"] or 0))
        if clip_start <= end and clip_end >= start:
            return row
    return None


def _attach_waiting_followups(conn, case_id: str, zone: str) -> list[str]:
    rows = conn.execute(
        "SELECT id,window_start,window_end FROM followups WHERE case_id=? AND target_zone=? AND status='awaiting_video' ORDER BY created_at",
        (case_id, zone),
    ).fetchall()
    attached: list[str] = []
    for row in rows:
        clip = _matching_video_for_window(
            conn, case_id, zone, _parse_timestamp(row["window_start"]), _parse_timestamp(row["window_end"])
        )
        if clip:
            conn.execute(
                "UPDATE followups SET video_source_id=?,status='pending' WHERE id=? AND status='awaiting_video'",
                (clip["video_source_id"], row["id"]),
            )
            attached.append(row["id"])
    return attached


def _match_score(detection: dict[str, Any], clue: dict[str, Any] | None) -> float | None:
    if not clue:
        return None
    targets = clue.get("visual_targets") or []
    if targets:
        observed_type = str(detection.get("subject_type") or detection.get("vehicle_type") or "").strip().lower()
        observed_color = str(detection.get("vehicle_color") or "").strip().lower()

        def canonical_type(value: str) -> str:
            normalized = value.strip().lower()
            if any(term in normalized for term in ("sedan", "coupe", "hatchback", "suv", "wagon", "van", "pickup", "automobile")):
                return "car"
            if any(term in normalized for term in ("motorcycle", "motorbike", "scooter", "moped")):
                return "motorcycle"
            if any(term in normalized for term in ("bicycle", "cycle")):
                return "bicycle"
            aliases = {
                "pedestrian": "person", "man": "person", "woman": "person", "cyclist": "person",
                "bike": "bicycle",
                "lorry": "truck",
            }
            return aliases.get(normalized, normalized)

        def canonical_color(value: str) -> str:
            normalized = value.strip().lower()
            if any(word in normalized for word in ("navy", "sky", "light blue", "dark blue", "blue")):
                return "blue"
            if any(word in normalized for word in ("maroon", "burgundy", "crimson", "red")):
                return "red"
            return normalized

        scores: list[float] = []
        for target in targets:
            expected_type = canonical_type(str(target.get("target_type") or ""))
            checks: list[float] = []
            if expected_type and expected_type != "other":
                checks.append(1.0 if canonical_type(observed_type) == expected_type else 0.0)
            expected_color = canonical_color(str(target.get("color") or ""))
            if expected_color:
                checks.append(1.0 if expected_color == canonical_color(observed_color) else 0.0)
            if checks:
                score = sum(checks) / len(checks)
                # A generic person-class hit is only a review candidate, not a match.
                if expected_type == "person" and not expected_color and score > 0:
                    score = 0.25
                scores.append(score)
        # Free-text marks/accessories are shown to a reviewer, never machine-scored.
        return max(scores) if scores else None

    checks: list[float] = []
    expected_color = (clue.get("vehicle_color") or "").strip().lower()
    if expected_color:
        observed_color = (detection.get("vehicle_color") or "").strip().lower()
        blue_terms = ("navy", "sky", "light blue", "dark blue", "blue")
        red_terms = ("maroon", "burgundy", "crimson", "red")
        if any(term in expected_color for term in blue_terms):
            expected_color = "blue"
        elif any(term in expected_color for term in red_terms):
            expected_color = "red"
        if any(term in observed_color for term in blue_terms):
            observed_color = "blue"
        elif any(term in observed_color for term in red_terms):
            observed_color = "red"
        checks.append(1.0 if expected_color == observed_color else 0.0)
    expected_type = (clue.get("vehicle_type") or "").strip().lower()
    if expected_type:
        observed_type = (detection.get("vehicle_type") or "").lower()
        def canonical_vehicle_type(value: str) -> str:
            normalized = value.lower()
            if any(term in normalized for term in ("sedan", "coupe", "hatchback", "suv", "wagon", "van", "pickup")):
                return "car"
            if any(term in normalized for term in ("motorcycle", "motorbike", "scooter", "bike")):
                return "motorcycle"
            if "bus" in normalized:
                return "bus"
            if "truck" in normalized or "lorry" in normalized:
                return "truck"
            return normalized
        checks.append(1.0 if canonical_vehicle_type(expected_type) == canonical_vehicle_type(observed_type) else 0.0)
    return sum(checks) / len(checks) if checks else None


def _matches_machine_clue(detection: dict[str, Any], clue: dict[str, Any] | None) -> bool:
    """Keep only detections satisfying every structured type/color part of the clue."""
    if not clue:
        return False
    targets = clue.get("visual_targets") or []
    if not targets:
        return _match_score(detection, clue) == 1.0

    def kind(value: str) -> str:
        text = value.strip().lower()
        if text in {"man", "woman", "pedestrian", "cyclist"}:
            return "person"
        if any(word in text for word in ("car", "sedan", "suv", "hatchback", "coupe", "van", "pickup", "wagon")):
            return "car"
        if any(word in text for word in ("bicycle", "cycle", "bike")):
            return "bicycle"
        if any(word in text for word in ("motorcycle", "motorbike", "scooter", "moped")):
            return "motorcycle"
        return text

    def color(value: str) -> str:
        text = value.strip().lower()
        if any(word in text for word in ("blue", "navy", "azure", "cyan")):
            return "blue"
        if any(word in text for word in ("red", "maroon", "burgundy", "crimson")):
            return "red"
        return text

    observed_type = kind(str(detection.get("subject_type") or detection.get("vehicle_type") or ""))
    observed_color = color(str(detection.get("vehicle_color") or ""))
    for target in targets:
        expected_type = str(target.get("target_type") or "").strip()
        expected_color = str(target.get("color") or "").strip()
        if not expected_type and not expected_color:
            continue
        if expected_type and kind(expected_type) != observed_type:
            continue
        if expected_color and color(expected_color) != observed_color:
            continue
        if expected_type or expected_color:
            return True
    return False


def _case_or_404(db_conn, case_id: str):
    row = db_conn.execute("SELECT * FROM cases WHERE case_id=?", (case_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Case not found. Create the case before processing evidence.")
    return row


def _case_zones(case: Any) -> set[str]:
    """Return only jurisdictions declared by this case's adjacency map."""
    neighbor_map = json.loads(case["neighbors_json"])
    zones = {case["initial_zone"], *neighbor_map.keys()}
    for adjacent in neighbor_map.values():
        zones.update(adjacent)
    return zones


def _video_runtime_status() -> dict[str, Any]:
    """Report optional video stack readiness without importing YOLO or loading weights."""
    modules = {"torch": "torch", "opencv": "cv2", "ultralytics": "ultralytics"}
    missing = [label for label, module in modules.items() if importlib.util.find_spec(module) is None]
    configured_device = os.getenv("YOLO_DEVICE", "auto")
    result: dict[str, Any] = {
        "missing_modules": missing,
        "configured_device": configured_device,
        "cuda_available": None,
        "cuda_device_name": None,
        "gpu_inference_ready": False,
    }
    if missing:
        return result
    try:
        import torch

        cuda_available = bool(torch.cuda.is_available())
        result["cuda_available"] = cuda_available
        if cuda_available:
            result["cuda_device_name"] = torch.cuda.get_device_name(0)
            result["gpu_inference_ready"] = configured_device.lower() != "cpu"
    except Exception as exc:
        result["runtime_error"] = type(exc).__name__
    return result


def _legacy_disabled() -> None:
    raise HTTPException(status_code=410, detail="Legacy endpoint is disabled in the authenticated MVP")


@app.get("/", include_in_schema=False)
def review_home():
    _legacy_disabled()


@app.get("/api-config.js", include_in_schema=False)
def api_config():
    _legacy_disabled()


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/v1/investigations/start", status_code=201)
async def start_investigation(request: CaseCreate, user: AuthenticatedUser = Depends(require_approved_officer)):
    _legacy_disabled()


def _serialize_video(row: Any) -> dict[str, Any]:
    return {
        "video_source_id": row["video_source_id"],
        "case_id": row["case_id"],
        "jurisdiction": row["jurisdiction"],
        "camera_id": row["camera_id"],
        "source_label": row.get("source_label") if isinstance(row, dict) else (row["source_label"] if "source_label" in row.keys() else None),
        "original_name": row["original_name"],
        "recorded_at": str(row["recorded_at"]) if row["recorded_at"] else None,
        "sha256": row["sha256"],
        "byte_size": int(row["byte_size"]),
        "duration_seconds": float(row["duration_seconds"] or 0.0),
        "width": row["width"] if "width" in row.keys() else None,
        "height": row["height"] if "height" in row.keys() else None,
        "fps": float(row["fps"]) if ("fps" in row.keys() and row["fps"] is not None) else None,
        "codec": row["codec"] if "codec" in row.keys() else None,
        "media_status": row["media_status"] if "media_status" in row.keys() else "ready",
        "created_at": str(row["created_at"]),
    }


@app.post("/api/v1/cases", status_code=201)
def create_case(
    request: CaseCreate,
    idempotency_key: str | None = Header(default=None, max_length=128),
    user: AuthenticatedUser = Depends(require_approved_officer),
):
    db.initialize()
    now = db.utc_now()
    idem = (idempotency_key or request.idempotency_key or "").strip() or None
    if request.case_id is not None:
        if db.runtime_env() != "test":
            raise HTTPException(status_code=422, detail="case_id is server-generated and cannot be supplied by clients")
        if not SAFE_CASE_ID_RE.fullmatch(request.case_id):
            raise HTTPException(status_code=422, detail="Invalid case_id format")
        case_id = request.case_id
    else:
        case_id = f"CASE-{uuid.uuid4().hex[:10].upper()}"
    neighbors = request.neighbors or {request.initial_zone: []}
    clue_envelope = (
        {"clue_notes": request.clue_notes, "incident_time_text": request.incident_time_text}
        if (request.clue_notes or request.incident_time_text)
        else None
    )
    with db.connection() as conn:
        if idem:
            dup = conn.execute(
                "SELECT case_id,title,description,clues_json,authorized_at,created_at,updated_at "
                "FROM cases WHERE owner_id=? AND idempotency_key=?",
                (user.user_id, idem),
            ).fetchone()
            if dup:
                state = conn.execute("SELECT status FROM investigation_states WHERE case_id=?", (dup["case_id"],)).fetchone()
                return {
                    "case_id": dup["case_id"],
                    "title": dup["title"],
                    "description": dup["description"],
                    "status": state["status"] if state else "draft",
                    "clues": json.loads(dup["clues_json"]) if dup["clues_json"] else None,
                    "authorized_at": str(dup["authorized_at"]) if dup["authorized_at"] else None,
                    "created_at": str(dup["created_at"]),
                    "updated_at": str(dup["updated_at"]) if dup["updated_at"] else None,
                    "idempotent": True,
                }
        existing = conn.execute("SELECT case_id FROM cases WHERE case_id=?", (case_id,)).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="case_id already exists")
        conn.execute(
            "INSERT INTO cases(case_id,owner_id,title,description,initial_zone,neighbors_json,clues_json,authorized_at,idempotency_key,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                case_id,
                user.user_id,
                request.title,
                request.description,
                request.initial_zone,
                db.canonical_json(neighbors),
                db.canonical_json(clue_envelope) if clue_envelope else None,
                now,
                idem,
                now,
                now,
            ),
        )
        conn.execute(
            "INSERT INTO investigation_states(case_id,status,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(case_id) DO NOTHING",
            (case_id, "draft", now),
        )
        db.audit(conn, "case_created", {"case_id": case_id, "owner_id": user.user_id})
    return {
        "case_id": case_id,
        "title": request.title,
        "description": request.description,
        "status": "draft",
        "clues": clue_envelope,
        "neighbors": neighbors,
        "authorized_at": now,
        "created_at": now,
        "updated_at": now,
    }


@app.patch("/api/v1/cases/{case_id}")
def update_case_draft(
    case_id: str,
    request: CaseUpdate,
    user: AuthenticatedUser = Depends(require_approved_officer),
):
    db.initialize()
    now = db.utc_now()
    with db.case_lock(case_id):
        with db.connection() as conn:
            db.lock_case_row(conn, case_id)
            row = dict(owned_case_or_404(conn, case_id, user.user_id))
            state = conn.execute("SELECT status FROM investigation_states WHERE case_id=?", (case_id,)).fetchone()
            if state and state["status"] != "draft":
                raise HTTPException(status_code=409, detail="Only draft investigations can be edited")
            existing_clues = json.loads(row["clues_json"]) if row.get("clues_json") else {}
            if request.clue_notes is not None:
                existing_clues["clue_notes"] = request.clue_notes or None
            if request.incident_time_text is not None:
                existing_clues["incident_time_text"] = request.incident_time_text or None
            new_title = request.title if request.title is not None else row.get("title")
            new_desc = request.description if request.description is not None else row["description"]
            clues_json = db.canonical_json(existing_clues) if existing_clues else None
            conn.execute(
                "UPDATE cases SET title=?, description=?, clues_json=?, updated_at=? WHERE case_id=? AND owner_id=?",
                (new_title, new_desc, clues_json, now, case_id, user.user_id),
            )
            db.audit(conn, "case_updated", {"case_id": case_id, "owner_id": user.user_id})
    return {
        "case_id": case_id,
        "title": new_title,
        "description": new_desc,
        "status": "draft",
        "clues": existing_clues or None,
        "updated_at": now,
    }


@app.get("/api/v1/cases")
def list_cases(user: AuthenticatedUser = Depends(require_approved_officer)):
    db.initialize()
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT case_id,title,description,clues_json,authorized_at,created_at,updated_at "
            "FROM cases WHERE owner_id=? ORDER BY created_at DESC",
            (user.user_id,),
        ).fetchall()
        items = []
        for row in rows:
            case_id = row["case_id"]
            state = conn.execute("SELECT status FROM investigation_states WHERE case_id=?", (case_id,)).fetchone()
            video_count = conn.execute("SELECT COUNT(*) AS total FROM videos WHERE case_id=?", (case_id,)).fetchone()["total"]
            finding_count = conn.execute("SELECT COUNT(*) AS total FROM sightings WHERE case_id=?", (case_id,)).fetchone()["total"]
            pending_count = conn.execute("SELECT COUNT(*) AS total FROM sightings WHERE case_id=? AND review_status='pending'", (case_id,)).fetchone()["total"]
            items.append({
                "case_id": case_id,
                "title": row["title"],
                "description": row["description"],
                "status": state["status"] if state else "draft",
                "clues": json.loads(row["clues_json"]) if row["clues_json"] else None,
                "video_count": int(video_count),
                "finding_count": int(finding_count),
                "pending_finding_count": int(pending_count),
                "authorized_at": row["authorized_at"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            })
    return items


@app.get("/api/v1/cases/{case_id}")
def get_case(case_id: str, user: AuthenticatedUser = Depends(require_approved_officer)):
    db.initialize()
    with db.connection() as conn:
        row = dict(owned_case_or_404(conn, case_id, user.user_id))
        state = conn.execute("SELECT status FROM investigation_states WHERE case_id=?", (case_id,)).fetchone()
        video_rows = conn.execute(
            "SELECT video_source_id,case_id,jurisdiction,camera_id,source_label,original_name,"
            "recorded_at,sha256,byte_size,duration_seconds,width,height,fps,codec,media_status,created_at "
            "FROM videos WHERE case_id=? ORDER BY created_at ASC",
            (case_id,),
        ).fetchall()
    return {
        "case_id": row["case_id"],
        "title": row.get("title"),
        "description": row["description"],
        "status": state["status"] if state else "draft",
        "clues": json.loads(row["clues_json"]) if row.get("clues_json") else None,
        "authorized_at": str(row["authorized_at"]) if row.get("authorized_at") else None,
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]) if row.get("updated_at") else None,
        "videos": [_serialize_video(v) for v in video_rows],
        "limits": {
            "max_upload_bytes": _max_upload_bytes(),
            "max_case_videos": _max_case_videos(),
            "supported_extensions": sorted(ALLOWED_EXTENSIONS),
        },
    }


@app.get("/api/v1/cases/{case_id}/jurisdictions")
def get_jurisdictions(case_id: str, user: AuthenticatedUser = Depends(require_approved_officer)):
    _legacy_disabled()


@app.post("/api/v1/cameras", status_code=201)
def register_camera(camera: CameraCreate, user: AuthenticatedUser = Depends(require_approved_officer)):
    _legacy_disabled()


def _validate_source_uri(uri: str) -> None:
    from urllib.parse import parse_qsl, urlsplit
    parsed = urlsplit(uri)
    secret_keys = ("password", "passwd", "pwd", "secret", "token", "apikey", "api_key")
    if parsed.username or parsed.password or any(key.lower() in secret_keys for key, _ in parse_qsl(parsed.query)):
        raise HTTPException(status_code=422, detail="Store source credentials separately from the URI")


@app.get("/api/v1/cameras")
def list_cameras(user: AuthenticatedUser = Depends(require_approved_officer)):
    _legacy_disabled()


@app.get("/api/v1/cameras/{camera_id}/nvr-recordings")
def discover_nvr_recordings(camera_id: str, user: AuthenticatedUser = Depends(require_approved_officer)):
    _legacy_disabled()


@app.post("/api/v1/cameras/{camera_id}/nvr-replay", status_code=201)
def register_nvr_replay(camera_id: str, request: NVRReplayCreate, user: AuthenticatedUser = Depends(require_approved_officer)):
    _legacy_disabled()


@app.post("/api/v1/recordings", status_code=201)
def register_recording(recording: RecordingCreate, user: AuthenticatedUser = Depends(require_approved_officer)):
    _legacy_disabled()


@app.get("/api/v1/recordings")
def list_recordings(case_id: str, jurisdiction: str | None = None, camera_id: str | None = None,
                    source_type: str | None = None, user: AuthenticatedUser = Depends(require_approved_officer)):
    _legacy_disabled()


@app.get("/api/v1/recordings/{recording_id}")
def get_recording(recording_id: str, user: AuthenticatedUser = Depends(require_approved_officer)):
    _legacy_disabled()


@app.post("/api/v1/recordings/{recording_id}/analyze")
def analyze_recording(recording_id: str, user: AuthenticatedUser = Depends(require_approved_officer)):
    _legacy_disabled()


@app.post("/api/v1/recordings/{recording_id}/ingest")
def ingest_recording(recording_id: str, seconds: int = 10, user: AuthenticatedUser = Depends(require_approved_officer)):
    _legacy_disabled()


@app.get("/api/v1/agents")
def list_agents(case_id: str, user: AuthenticatedUser = Depends(require_approved_officer)):
    _legacy_disabled()


@app.post("/api/v1/cases/{case_id}/plan")
async def plan_case(case_id: str, idempotency_key: str | None = Header(default=None, max_length=128),
                    user: AuthenticatedUser = Depends(require_approved_officer)):
    with db.case_lock(case_id):
        with db.connection() as conn:
            owned_case_or_404(conn, case_id, user.user_id)
        try:
            from .orchestration import InvestigationConflictError, plan_investigation
            return await plan_investigation(case_id, idempotency_key)
        except InvestigationConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ImportError as exc:
            raise HTTPException(status_code=503, detail="LangGraph is not installed") from exc
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise HTTPException(status_code=503, detail="Ollama is unavailable") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/v1/cases/{case_id}/progress")
def case_progress(case_id: str, user: AuthenticatedUser = Depends(require_approved_officer)):
    db.initialize()
    with db.connection() as conn:
        case = dict(owned_case_or_404(conn, case_id, user.user_id))
        run_row = conn.execute(
            "SELECT id, status, input_video_ids, error_code, started_at, finished_at, updated_at, state_json "
            "FROM orchestration_runs WHERE case_id=? ORDER BY created_at DESC LIMIT 1",
            (case_id,),
        ).fetchone()
        job_rows = conn.execute(
            "SELECT j.id, j.run_id, j.video_source_id, j.worker_id, j.status, j.attempts, "
            "j.error_detail, j.heartbeat_at, j.started_at, j.finished_at, j.updated_at, "
            "v.source_label, v.original_name "
            "FROM investigation_jobs j LEFT JOIN videos v ON v.video_source_id=j.video_source_id "
            "WHERE j.case_id=? ORDER BY j.created_at",
            (case_id,),
        ).fetchall()
        jobs = [
            {
                "id": row["id"],
                "run_id": row["run_id"],
                "video_source_id": row["video_source_id"],
                "source_label": row["source_label"] or row["original_name"],
                "worker_id": row["worker_id"],
                "status": row["status"],
                "attempts": int(row["attempts"] or 0),
                "error_detail": row["error_detail"],
                "heartbeat_at": str(row["heartbeat_at"]) if row["heartbeat_at"] else None,
                "started_at": str(row["started_at"]) if row["started_at"] else None,
                "finished_at": str(row["finished_at"]) if row["finished_at"] else None,
                "updated_at": str(row["updated_at"]),
            }
            for row in job_rows
        ]
        count = conn.execute("SELECT COUNT(*) AS total FROM sightings WHERE case_id=?", (case_id,)).fetchone()["total"]
        state = conn.execute("SELECT status,updated_at FROM investigation_states WHERE case_id=?", (case_id,)).fetchone()
    run_info = None
    if run_row:
        raw_ids = run_row["input_video_ids"]
        input_ids = json.loads(raw_ids) if isinstance(raw_ids, str) else raw_ids
        run_state = json.loads(run_row["state_json"]) if run_row["state_json"] else {}
        run_info = {
            "id": run_row["id"],
            "status": run_row["status"],
            "input_video_ids": input_ids or [],
            "error_code": run_row["error_code"],
            "error_detail": run_state.get("error_detail"),
            "started_at": str(run_row["started_at"]) if run_row["started_at"] else None,
            "finished_at": str(run_row["finished_at"]) if run_row["finished_at"] else None,
            "updated_at": str(run_row["updated_at"]),
        }
    return {
        "case_id": case_id,
        "title": case.get("title"),
        "description": case["description"],
        "clue": json.loads(case["clues_json"]) if case.get("clues_json") else None,
        "status": state["status"] if state else "draft",
        "status_updated_at": str(state["updated_at"]) if state else None,
        "run": run_info,
        "total_videos": len(run_info["input_video_ids"]) if (run_info and run_info["input_video_ids"]) else len(jobs),
        "queued_jobs": sum(1 for j in jobs if j["status"] == "queued"),
        "running_jobs": sum(1 for j in jobs if j["status"] == "running"),
        "retrying_jobs": sum(1 for j in jobs if j["status"] == "retrying"),
        "completed_jobs": sum(1 for j in jobs if j["status"] == "completed"),
        "failed_jobs": sum(1 for j in jobs if j["status"] == "failed"),
        "jobs": jobs,
        "sighting_count": int(count),
    }


@app.put("/api/v1/cases/{case_id}/status")
def update_case_status(case_id: str, request: InvestigationStatusUpdate, user: AuthenticatedUser = Depends(require_approved_officer)):
    _legacy_disabled()


@app.get("/api/v1/evidence")
def list_evidence(case_id: str, user: AuthenticatedUser = Depends(require_approved_officer)):
    with db.connection() as conn:
        owned_case_or_404(conn, case_id, user.user_id)
        rows = conn.execute("SELECT id,video_source_id,occurred_at,video_offset_seconds,evidence_sha256,frame_sha256,review_status FROM sightings WHERE case_id=? ORDER BY occurred_at", (case_id,)).fetchall()
    return [dict(row) for row in rows]


@app.post("/api/v1/clues/parse")
async def parse_clues(request: ClueParseRequest, user: AuthenticatedUser = Depends(require_approved_officer)):
    db.initialize()
    with db.connection() as conn:
        case = owned_case_or_404(conn, request.case_id, user.user_id)
        state = conn.execute("SELECT status FROM investigation_states WHERE case_id=?", (request.case_id,)).fetchone()
        if state and state["status"] != "draft":
            raise HTTPException(
                status_code=409,
                detail=f"Investigation criteria cannot be modified once processing has begun. Current status: '{state['status']}'",
            )
        saved_initial_zone = case["initial_zone"]
        previous_clue_json = case["clues_json"]
    try:
        clue = await parse_vehicle_clues(request.case_id, request.description, saved_initial_zone)
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise HTTPException(status_code=503, detail=f"Local Ollama is not available at {OLLAMA_BASE_URL}") from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Ollama returned HTTP {exc.response.status_code}") from exc
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail="Ollama response did not match the clue schema") from exc
    with db.connection() as conn:
        state = conn.execute("SELECT status FROM investigation_states WHERE case_id=?", (request.case_id,)).fetchone()
        if state and state["status"] != "draft":
            raise HTTPException(
                status_code=409,
                detail="Investigation criteria cannot be modified once processing has begun",
            )
        if previous_clue_json:
            conn.execute("UPDATE sightings SET clue_json=? WHERE case_id=? AND clue_json IS NULL",
                         (previous_clue_json, request.case_id))
        cur = conn.execute(
            "UPDATE cases SET description=?,clues_json=?,updated_at=? "
            "WHERE case_id=? AND owner_id=? AND "
            "(SELECT status FROM investigation_states WHERE case_id=cases.case_id) = 'draft'",
            (request.description, clue.model_dump_json(), db.utc_now(), request.case_id, user.user_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(
                status_code=409,
                detail="Investigation criteria cannot be modified once processing has begun",
            )
        db.audit(conn, "case_clue_updated", {"case_id": request.case_id, "owner_id": user.user_id,
                                               "parser": clue.parser, "clue": clue.model_dump()})
        db.emit_case_event(conn, request.case_id, "case_changed")
    return clue.model_dump()


@app.post("/api/v1/clues/transcribe")
async def transcribe_clue_audio(
    user: AuthenticatedUser = Depends(require_approved_officer),
    file: UploadFile = File(...),
):
    _legacy_disabled()


@app.post("/api/v1/videos", status_code=201)
async def upload_video(
    user: AuthenticatedUser = Depends(require_approved_officer),
    case_id: str = Form(...),
    file: UploadFile = File(...),
    jurisdiction: str | None = Form(default=None),
    camera_id: str | None = Form(default=None),
    source_label: str | None = Form(default=None),
    recorded_at: str | None = Form(default=None),
    upload_key: str | None = Form(default=None),
    idempotency_key: str | None = Header(default=None, max_length=128),
):
    db.initialize()
    if not SAFE_CASE_ID_RE.fullmatch(case_id):
        raise HTTPException(status_code=422, detail="Invalid case_id format")
    safe_orig = Path(file.filename or "recording.mp4").name
    suffix = Path(safe_orig).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail=f"Supported video extensions: {', '.join(sorted(ALLOWED_EXTENSIONS))}")

    recorded_iso: str | None = None
    if recorded_at and recorded_at.strip():
        try:
            recorded_iso = _iso(datetime.fromisoformat(recorded_at.strip().replace("Z", "+00:00")))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="recorded_at must be an ISO-8601 timestamp") from exc

    idem_key = (upload_key or idempotency_key or "").strip() or None
    max_videos = _max_case_videos()
    max_bytes = _max_upload_bytes()

    with db.connection() as conn:
        case = owned_case_or_404(conn, case_id, user.user_id)
        state = conn.execute("SELECT status FROM investigation_states WHERE case_id=?", (case_id,)).fetchone()
        if state and state["status"] != "draft":
            raise HTTPException(status_code=409, detail="Videos can only be uploaded to draft investigations")
        resolved_zone = (jurisdiction or "").strip() or case["initial_zone"]
        if resolved_zone not in _case_zones(case):
            raise HTTPException(status_code=422, detail="Jurisdiction is not part of this case's zone map")

    resolved_cam = (camera_id or "").strip() or f"CAM-{uuid.uuid4().hex[:8].upper()}"
    clean_label = (source_label or "").strip()[:120] or safe_orig[:120]
    video_id = str(uuid.uuid4())
    evidence_root = db.EVIDENCE_DIR.resolve(strict=False)
    cases_root = (evidence_root / "cases").resolve(strict=False)
    case_dir = (cases_root / case_id).resolve(strict=False)
    dest = (case_dir / f"{video_id}{suffix}").resolve(strict=False)
    staging_dest = (case_dir / f".staging-{video_id}{suffix}").resolve(strict=False)
    if not case_dir.is_relative_to(cases_root) or not dest.is_relative_to(case_dir) or not staging_dest.is_relative_to(case_dir):
        raise HTTPException(status_code=422, detail="Invalid investigation storage path")
    case_dir.mkdir(parents=True, exist_ok=True)
    storage_key = f"cases/{case_id}/{video_id}{suffix}"
    digest = hashlib.sha256()
    total = 0
    with _ACTIVE_STAGING_GUARD:
        _ACTIVE_STAGING_PATHS.add(staging_dest)
    try:
        try:
            with staging_dest.open("wb") as out:
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > max_bytes:
                        raise HTTPException(status_code=413, detail=f"Video exceeds configured upload limit ({max_bytes // (1024 * 1024)} MB)")
                    digest.update(chunk)
                    out.write(chunk)
            if total == 0:
                raise HTTPException(status_code=422, detail="Uploaded video file is empty")
        finally:
            await file.close()

        sha = digest.hexdigest()
        with db.case_lock(case_id):
            with db.connection() as conn:
                db.lock_case_row(conn, case_id)
                state = conn.execute("SELECT status FROM investigation_states WHERE case_id=?", (case_id,)).fetchone()
                if state and state["status"] != "draft":
                    raise HTTPException(status_code=409, detail="Videos can only be uploaded to draft investigations")
                if idem_key:
                    dup = conn.execute(
                        "SELECT video_source_id,case_id,jurisdiction,camera_id,source_label,original_name,"
                        "recorded_at,sha256,byte_size,duration_seconds,width,height,fps,codec,media_status,created_at "
                        "FROM videos WHERE case_id=? AND upload_key=?",
                        (case_id, idem_key),
                    ).fetchone()
                    if dup:
                        if dup["sha256"] != sha:
                            raise HTTPException(status_code=409, detail="Idempotency key already used for different video content")
                        staging_dest.unlink(missing_ok=True)
                        return {**_serialize_video(dup), "status": "ready", "idempotent": True}
                count_row = conn.execute("SELECT COUNT(*) AS total FROM videos WHERE case_id=?", (case_id,)).fetchone()
                if int(count_row["total"]) >= max_videos:
                    raise HTTPException(status_code=409, detail=f"Maximum of {max_videos} videos per investigation reached")

        try:
            probe = validate_and_probe_uploaded_video(staging_dest, suffix)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ImportError as exc:
            raise HTTPException(status_code=503, detail="Install requirements-video.txt to accept video files") from exc

        now = db.utc_now()
        with db.case_lock(case_id):
            with db.connection() as conn:
                db.lock_case_row(conn, case_id)
                state = conn.execute("SELECT status FROM investigation_states WHERE case_id=?", (case_id,)).fetchone()
                if state and state["status"] != "draft":
                    raise HTTPException(status_code=409, detail="Videos can only be uploaded to draft investigations")
                if idem_key:
                    dup = conn.execute(
                        "SELECT video_source_id,case_id,jurisdiction,camera_id,source_label,original_name,"
                        "recorded_at,sha256,byte_size,duration_seconds,width,height,fps,codec,media_status,created_at "
                        "FROM videos WHERE case_id=? AND upload_key=?",
                        (case_id, idem_key),
                    ).fetchone()
                    if dup:
                        if dup["sha256"] != sha:
                            raise HTTPException(status_code=409, detail="Idempotency key already used for different video content")
                        staging_dest.unlink(missing_ok=True)
                        return {**_serialize_video(dup), "status": "ready", "idempotent": True}
                count_row = conn.execute("SELECT COUNT(*) AS total FROM videos WHERE case_id=?", (case_id,)).fetchone()
                if int(count_row["total"]) >= max_videos:
                    raise HTTPException(status_code=409, detail=f"Maximum of {max_videos} videos per investigation reached")
                conn.execute(
                    "INSERT INTO videos(video_source_id,case_id,jurisdiction,camera_id,original_name,file_path,"
                    "recorded_at,sha256,byte_size,duration_seconds,upload_key,source_label,storage_kind,storage_key,"
                    "media_status,width,height,fps,codec,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        video_id, case_id, resolved_zone, resolved_cam, safe_orig, str(dest),
                        recorded_iso, sha, total, probe["duration_seconds"], idem_key, clean_label,
                        "local_private", storage_key, "ready", probe["width"], probe["height"], probe["fps"], probe["codec"], now,
                    ),
                )
                conn.execute(
                    "INSERT INTO recordings(id,case_id,camera_id,jurisdiction,source_type,video_source_id,recorded_at,status,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (video_id, case_id, resolved_cam, resolved_zone, "upload", video_id, recorded_iso, "ready", now),
                )
                conn.execute(
                    "INSERT INTO video_metadata(video_source_id,metadata_json) VALUES(?,?) "
                    "ON CONFLICT(video_source_id) DO UPDATE SET metadata_json=excluded.metadata_json",
                    (video_id, db.canonical_json(probe["metadata"])),
                )
                db.audit(
                    conn,
                    "video_registered",
                    {
                        "video_source_id": video_id,
                        "case_id": case_id,
                        "jurisdiction": resolved_zone,
                        "camera_id": resolved_cam,
                        "sha256": sha,
                        "byte_size": total,
                        "duration_seconds": probe["duration_seconds"],
                    },
                )
                staging_dest.replace(dest)
    except Exception:
        staging_dest.unlink(missing_ok=True)
        dest.unlink(missing_ok=True)
        raise
    finally:
        with _ACTIVE_STAGING_GUARD:
            _ACTIVE_STAGING_PATHS.discard(staging_dest)
    return {
        "video_source_id": video_id,
        "case_id": case_id,
        "jurisdiction": resolved_zone,
        "camera_id": resolved_cam,
        "source_label": clean_label,
        "original_name": safe_orig,
        "recorded_at": recorded_iso,
        "sha256": sha,
        "byte_size": total,
        "duration_seconds": probe["duration_seconds"],
        "width": probe["width"],
        "height": probe["height"],
        "fps": probe["fps"],
        "codec": probe["codec"],
        "media_status": "ready",
        "status": "ready",
        "created_at": now,
    }


@app.get("/api/v1/videos")
def list_videos(case_id: str | None = None, jurisdiction: str | None = None, user: AuthenticatedUser = Depends(require_approved_officer)):
    db.initialize()
    query = (
        "SELECT v.video_source_id,v.case_id,v.jurisdiction,v.camera_id,v.source_label,v.original_name,"
        "v.recorded_at,v.sha256,v.byte_size,v.duration_seconds,v.width,v.height,v.fps,v.codec,v.media_status,v.created_at "
        "FROM videos v JOIN cases c ON c.case_id=v.case_id WHERE c.owner_id=?"
    )
    params: list[str] = [user.user_id]
    if case_id:
        query += " AND v.case_id=?"
        params.append(case_id)
    if jurisdiction:
        query += " AND v.jurisdiction=?"
        params.append(jurisdiction)
    query += " ORDER BY v.created_at ASC"
    with db.connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_serialize_video(row) for row in rows]


@app.delete("/api/v1/videos/{video_source_id}")
def delete_draft_video(video_source_id: str, user: AuthenticatedUser = Depends(require_approved_officer)):
    db.initialize()
    with db.connection() as conn:
        pre = conn.execute(
            "SELECT v.case_id FROM videos v JOIN cases c ON c.case_id=v.case_id WHERE v.video_source_id=? AND c.owner_id=?",
            (video_source_id, user.user_id),
        ).fetchone()
    if pre is None:
        raise HTTPException(status_code=404, detail="Video not found")
    case_id = pre["case_id"]
    with db.case_lock(case_id):
        with db.connection() as conn:
            db.lock_case_row(conn, case_id)
            row = conn.execute(
                "SELECT v.video_source_id,v.case_id,v.file_path,s.status AS case_status "
                "FROM videos v JOIN cases c ON c.case_id=v.case_id "
                "LEFT JOIN investigation_states s ON s.case_id=c.case_id "
                "WHERE v.video_source_id=? AND c.owner_id=?",
                (video_source_id, user.user_id),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Video not found")
            if row["case_status"] and row["case_status"] != "draft":
                raise HTTPException(status_code=409, detail="Only videos in draft investigations can be removed")
            file_path = Path(row["file_path"])
            conn.execute("DELETE FROM recordings WHERE video_source_id=?", (video_source_id,))
            conn.execute("DELETE FROM video_metadata WHERE video_source_id=?", (video_source_id,))
            conn.execute("DELETE FROM videos WHERE video_source_id=?", (video_source_id,))
            db.audit(conn, "video_removed", {"video_source_id": video_source_id, "case_id": row["case_id"]})
    try:
        resolved = file_path.resolve(strict=False)
        if resolved.is_relative_to(db.EVIDENCE_DIR.resolve(strict=False)):
            resolved.unlink(missing_ok=True)
    except Exception:
        pass
    return {"deleted": True, "video_source_id": video_source_id}


@app.post("/api/v1/work-items/resolve")
def resolve_work_items(request: ResolveWorkItemsRequest, user: AuthenticatedUser = Depends(require_approved_officer)):
    _legacy_disabled()


@app.get("/api/v1/videos/{video_source_id}/file")
def get_video_file(
    video_source_id: str,
    range_header: str | None = Header(default=None, alias="Range"),
    user: AuthenticatedUser = Depends(require_approved_officer),
):
    db.initialize()
    with db.connection() as conn:
        row = conn.execute(
            "SELECT v.file_path,v.original_name FROM videos v JOIN cases c ON c.case_id=v.case_id "
            "WHERE v.video_source_id=? AND c.owner_id=?",
            (video_source_id, user.user_id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Video not found")
    video_path = Path(row["file_path"]).resolve(strict=False)
    evidence_root = db.EVIDENCE_DIR.resolve(strict=False)
    if not video_path.is_relative_to(evidence_root) or not video_path.is_file():
        raise HTTPException(status_code=404, detail="Video not found")

    file_size = video_path.stat().st_size
    media_type = mimetypes.guess_type(row["original_name"])[0] or "video/mp4"
    base_headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": "inline",
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    }
    if not range_header or not range_header.lower().startswith("bytes="):
        return FileResponse(video_path, media_type=media_type, headers=base_headers)

    spec = range_header[6:].split(",", 1)[0].strip()
    if "-" not in spec:
        raise HTTPException(status_code=416, detail="Invalid Range header")
    start_str, end_str = spec.split("-", 1)
    try:
        if start_str == "":
            suffix_len = int(end_str)
            start = max(0, file_size - suffix_len)
            end = file_size - 1
        else:
            start = int(start_str)
            end = int(end_str) if end_str else file_size - 1
    except ValueError as exc:
        raise HTTPException(status_code=416, detail="Invalid Range header") from exc

    if start < 0 or end >= file_size or start > end:
        raise HTTPException(status_code=416, detail="Requested range not satisfiable")

    length = end - start + 1

    def iter_range():
        with video_path.open("rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                chunk = fh.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(
        iter_range(),
        status_code=206,
        media_type=media_type,
        headers={
            **base_headers,
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(length),
        },
    )


@app.get("/api/v1/sightings/{sighting_id}/frame")
def get_sighting_frame(sighting_id: str, annotated: bool = False, user: AuthenticatedUser = Depends(require_approved_officer)):
    from fastapi.responses import Response
    from .storage import StorageConfigError, StorageOperationError, SupabaseEvidenceStorage

    db.initialize()
    with db.connection() as conn:
        row = conn.execute(
            "SELECT s.frame_path, s.frame_storage_key, s.frame_sha256, s.visual_attributes_json, s.subject_type, s.vehicle_type, s.detector_confidence "
            "FROM sightings s JOIN cases c ON c.case_id=s.case_id "
            "WHERE s.id=? AND c.owner_id=?",
            (sighting_id, user.user_id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    evidence_root = db.EVIDENCE_DIR.resolve(strict=False)
    raw_bytes: bytes | None = None
    if row["frame_path"]:
        frame_path = Path(row["frame_path"]).resolve(strict=False)
        if frame_path.is_relative_to(evidence_root) and frame_path.is_file():
            raw_bytes = frame_path.read_bytes()
    if raw_bytes is None and row["frame_storage_key"]:
        try:
            storage = SupabaseEvidenceStorage()
            raw_bytes = storage.download_frame(row["frame_storage_key"])
        except (StorageConfigError, StorageOperationError):
            pass
    if raw_bytes is None:
        raise HTTPException(status_code=404, detail="No unannotated captured evidence frame found for this stored candidate")

    # Authoritative evidence hash verification (Task 6.0 A)
    expected_sha256 = row["frame_sha256"] if "frame_sha256" in row.keys() else None
    if not expected_sha256:
        raise HTTPException(
            status_code=409,
            detail="Captured evidence integrity check failed: missing stored frame hash",
        )
    actual_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    if actual_sha256 != expected_sha256:
        raise HTTPException(
            status_code=409,
            detail=f"Captured evidence integrity check failed: stored frame hash mismatch ({actual_sha256} != {expected_sha256})",
        )

    # Secure, no-store cache control headers to prevent stale evidence persistence (Task 6.0 A)
    base_headers = {
        "Cache-Control": "private, no-store, no-cache, must-revalidate",
        "Pragma": "no-cache",
        "X-Frame-SHA256": actual_sha256,
        "X-Evidence-Integrity": "verified",
    }

    if not annotated:
        return Response(
            content=raw_bytes,
            media_type="image/jpeg",
            headers={**base_headers, "X-Evidence-Type": "unannotated_captured_frame"},
        )

    # Dynamic annotated evidence visualization (Task 6.0 E)
    import cv2
    import numpy as np

    nparr = np.frombuffer(raw_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(status_code=500, detail="Failed to decode captured evidence frame for annotation")

    attrs = {}
    if row["visual_attributes_json"]:
        try:
            attrs = json.loads(row["visual_attributes_json"])
        except Exception:
            attrs = {}

    bbox = None
    if isinstance(attrs, dict):
        bbox = attrs.get("scaled_bbox") or attrs.get("bbox")

    if not bbox or not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise HTTPException(
            status_code=422,
            detail="Bounding-box annotation metadata is unavailable for this captured evidence frame",
        )

    img_h, img_w = img.shape[:2]
    try:
        x1, y1, x2, y2 = [int(round(float(v))) for v in bbox]
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=422,
            detail="Bounding-box coordinates must be numeric values",
        ) from exc

    # Validate coordinates against actual image dimensions (Task 6.0 E)
    if x1 < 0 or y1 < 0 or x2 > img_w or y2 > img_h or x1 >= x2 or y1 >= y2:
        raise HTTPException(
            status_code=422,
            detail=f"Bounding-box coordinates {[x1, y1, x2, y2]} are invalid or exceed frame dimensions ({img_w}x{img_h})",
        )

    cv2.rectangle(img, (x1, y1), (x2, y2), (40, 220, 120), max(2, img.shape[1] // 900))
    label_type = row["subject_type"] or row["vehicle_type"] or "target"
    conf = float(row["detector_confidence"] or 0.0)
    label = f"{label_type} {conf:.2f}"
    cv2.putText(
        img,
        label,
        (max(8, x1), max(24, y1 - 8)),
        cv2.FONT_HERSHEY_SIMPLEX,
        max(0.55, img.shape[1] / 1800),
        (40, 220, 120),
        2,
        cv2.LINE_AA,
    )
    encoded, jpeg = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    if not encoded:
        raise HTTPException(status_code=500, detail="Failed to encode annotated evidence visualization")

    return Response(
        content=jpeg.tobytes(),
        media_type="image/jpeg",
        headers={**base_headers, "X-Evidence-Type": "annotated_evidence_visualization"},
    )


@app.post("/api/v1/zones/analyze")
async def analyze_zones(request: AnalyzeZonesRequest, user: AuthenticatedUser = Depends(require_approved_officer)):
    _legacy_disabled()


@app.post("/api/v1/zone/analyze")
def analyze_zone(request: AnalyzeZoneRequest, user: AuthenticatedUser = Depends(require_approved_officer)):
    _legacy_disabled()


def _assert_job_lease(conn, job_id: str | None, lease_token: str | None) -> None:
    if not job_id:
        return
    if isinstance(conn, db.PostgresConnection):
        row = conn.execute(
            "SELECT lease_expires_at FROM investigation_jobs WHERE id=? AND status='running' AND lease_token=? FOR UPDATE",
            (job_id, lease_token),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT lease_expires_at FROM investigation_jobs WHERE id=? AND status='running' AND lease_token=?",
            (job_id, lease_token),
        ).fetchone()
    if row is None:
        raise RuntimeError("Processing lease was lost before evidence commit")
    if row["lease_expires_at"] and str(row["lease_expires_at"]) < db.utc_now():
        raise RuntimeError("Processing lease expired before evidence commit")


def _analyze_zone(request: AnalyzeZoneRequest, job_id: str | None = None, lease_token: str | None = None):
    from .storage import StorageConfigError, SupabaseEvidenceStorage, build_evidence_frame_key

    db.initialize()
    with db.connection() as conn:
        _assert_job_lease(conn, job_id, lease_token)
        case = _case_or_404(conn, request.case_id)
        if request.jurisdiction not in _case_zones(case):
            raise HTTPException(status_code=422, detail="Jurisdiction is not part of this case's zone map")
        video = conn.execute("SELECT * FROM videos WHERE video_source_id=?", (request.video_source_id,)).fetchone()
        if video is None:
            raise HTTPException(status_code=404, detail="video_source_id is not registered in the local evidence store")
        if video["case_id"] != request.case_id or video["jurisdiction"] != request.jurisdiction:
            raise HTTPException(status_code=409, detail="Video case or jurisdiction does not match this worker assignment")
        run_id = getattr(request, "run_id", None)
        if job_id and not run_id:
            job_row = conn.execute("SELECT run_id FROM investigation_jobs WHERE id=?", (job_id,)).fetchone()
            run_id = job_row["run_id"] if job_row else None
        if run_id:
            run_row = conn.execute("SELECT input_video_ids FROM orchestration_runs WHERE id=?", (run_id,)).fetchone()
            if run_row and run_row["input_video_ids"]:
                raw_ids = run_row["input_video_ids"]
                allowed_ids = json.loads(raw_ids) if isinstance(raw_ids, str) else list(raw_ids)
                if request.video_source_id not in allowed_ids:
                    raise HTTPException(status_code=409, detail="Video does not belong to the reserved investigation snapshot")
        clue = request.clue.model_dump() if request.clue else (json.loads(case["clues_json"]) if case["clues_json"] else None)

    evidence_root = db.EVIDENCE_DIR.resolve(strict=False)
    video_path = Path(video["file_path"]).resolve(strict=False)
    if not video_path.is_relative_to(evidence_root) or not video_path.is_file():
        raise HTTPException(status_code=404, detail="Source video file is missing from private evidence storage")
    actual_sha = hashlib.sha256(video_path.read_bytes()).hexdigest()
    if actual_sha != video["sha256"]:
        raise HTTPException(status_code=409, detail="Source video integrity check failed")

    recorded_dt = datetime.fromisoformat(str(video["recorded_at"])) if video["recorded_at"] else None
    try:
        detections = analyze_video(video_path, recorded_dt, float(os.getenv("YOLO_CONFIDENCE", "0.30")))
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="OpenCV/Ultralytics are not installed; install requirements and configure YOLO") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Video analysis failed: {type(exc).__name__}: {exc}") from exc

    sightings: list[dict[str, Any]] = []
    supabase_storage_configured = bool(
        os.getenv("SUPABASE_URL", "").strip()
        and (os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip() or os.getenv("SUPABASE_SECRET_KEY", "").strip())
    )
    storage_client: SupabaseEvidenceStorage | None = None
    owner_id = dict(case).get("owner_id")
    if supabase_storage_configured and owner_id:
        storage_client = SupabaseEvidenceStorage()

    with db.connection() as conn:
        _assert_job_lease(conn, job_id, lease_token)
        for detection in detections:
            _assert_job_lease(conn, job_id, lease_token)
            score = _match_score(detection, clue)
            if not _matches_machine_clue(detection, clue):
                continue
            score = 1.0
            track_id = detection.get("track_id")
            frame_offset = float(detection.get("frame_offset_seconds", detection["start_offset_seconds"]))
            subject_type = detection.get("subject_type") or detection.get("vehicle_type") or "vehicle"
            candidate_key = (
                f"{request.video_source_id}:t{track_id}"
                if track_id is not None
                else f"{request.video_source_id}:{subject_type}:{int(round(frame_offset * 1000))}"
            )
            if job_id and candidate_key:
                existing_cand = conn.execute(
                    "SELECT id FROM sightings WHERE job_id=? AND candidate_key=?",
                    (job_id, candidate_key),
                ).fetchone()
                if existing_cand:
                    continue

            sighting_id = (
                str(uuid.uuid5(uuid.NAMESPACE_URL, f"sighting:{job_id}:{candidate_key}"))
                if job_id and candidate_key
                else str(uuid.uuid4())
            )
            visual_attributes = detection.get("visual_attributes") or []
            bbox = detection.get("bbox")
            scaled_bbox = detection.get("scaled_bbox") or bbox
            frame_w = detection.get("frame_width")
            frame_h = detection.get("frame_height")
            saved_w = detection.get("saved_frame_width") or frame_w
            saved_h = detection.get("saved_frame_height") or frame_h
            attr_payload = {
                "tags": visual_attributes if isinstance(visual_attributes, list) else [],
                "bbox": bbox,
                "scaled_bbox": scaled_bbox,
                "frame_width": frame_w,
                "frame_height": frame_h,
                "saved_frame_width": saved_w,
                "saved_frame_height": saved_h,
                "target_type": subject_type,
                "color": detection.get("vehicle_color"),
                "confidence": detection.get("detector_confidence"),
            }
            frame_bytes = detection.get("frame_jpeg")
            occurred = _iso(recorded_dt + timedelta(seconds=frame_offset)) if recorded_dt is not None else None
            frame_path = None
            frame_sha256 = None
            frame_storage_key = None
            if frame_bytes:
                frame_dir = db.EVIDENCE_DIR / "frames"
                frame_dir.mkdir(parents=True, exist_ok=True)
                frame_path = frame_dir / f"{sighting_id}.jpg"
                frame_path.write_bytes(frame_bytes)
                frame_sha256 = hashlib.sha256(frame_bytes).hexdigest()
                if storage_client is not None and owner_id:
                    frame_storage_key = build_evidence_frame_key(
                        str(owner_id),
                        request.case_id,
                        request.video_source_id,
                        sighting_id,
                    )
                    storage_client.upload_frame(frame_storage_key, frame_bytes)
            integrity_payload = {
                "video_sha256": video["sha256"],
                "case_id": request.case_id,
                "worker_id": request.worker_id,
                "jurisdiction": request.jurisdiction,
                "camera_id": video["camera_id"],
                "occurred_at": occurred,
                "video_offset_seconds": frame_offset,
                "vehicle_type": detection["vehicle_type"],
                "vehicle_color": detection.get("vehicle_color"),
                "detector_confidence": detection["detector_confidence"],
                "subject_type": subject_type,
                "visual_attributes": attr_payload,
                "frame_sha256": frame_sha256,
                "clue": clue,
            }
            evidence_hash = hashlib.sha256((video["sha256"] + db.canonical_json(integrity_payload)).encode("utf-8")).hexdigest()
            conn.execute(
                "INSERT INTO sightings(id,case_id,job_id,candidate_key,worker_id,jurisdiction,camera_id,video_source_id,occurred_at,video_offset_seconds,track_id,vehicle_type,vehicle_color,subject_type,visual_attributes_json,integrity_version,clue_json,frame_path,frame_storage_key,frame_sha256,detector_confidence,clue_match_score,evidence_sha256,review_status,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
                (
                    sighting_id,
                    request.case_id,
                    job_id,
                    candidate_key,
                    request.worker_id,
                    request.jurisdiction,
                    video["camera_id"],
                    request.video_source_id,
                    occurred,
                    frame_offset,
                    track_id,
                    detection["vehicle_type"],
                    detection.get("vehicle_color"),
                    subject_type,
                    db.canonical_json(attr_payload),
                    4,
                    db.canonical_json(clue) if clue else None,
                    str(frame_path) if frame_path else None,
                    frame_storage_key,
                    frame_sha256,
                    detection["detector_confidence"],
                    score,
                    evidence_hash,
                    "pending",
                    db.utc_now(),
                ),
            )
            sightings.append(
                {
                    "id": sighting_id,
                    "case_id": request.case_id,
                    "worker_id": request.worker_id,
                    "jurisdiction": request.jurisdiction,
                    "camera_id": video["camera_id"],
                    "video_source_id": request.video_source_id,
                    "occurred_at": occurred,
                    "video_offset_seconds": frame_offset,
                    "track_id": track_id,
                    "vehicle_type": detection["vehicle_type"],
                    "vehicle_color": detection.get("vehicle_color"),
                    "subject_type": subject_type,
                    "visual_attributes": visual_attributes,
                    "attributes_require_human_review": True,
                    "detector_confidence": detection["detector_confidence"],
                    "clue_match_score": score,
                    "frame_url": f"/api/v1/sightings/{sighting_id}/frame" if (frame_path or frame_storage_key) else None,
                    "frame_sha256": frame_sha256,
                    "evidence_sha256": evidence_hash,
                    "review_status": "pending",
                }
            )
        db.audit(
            conn,
            "zone_analysis_completed",
            {
                "case_id": request.case_id,
                "worker_id": request.worker_id,
                "jurisdiction": request.jurisdiction,
                "video_source_id": request.video_source_id,
                "sighting_count": len(sightings),
            },
        )
        if job_id:
            now = db.utc_now()
            finished = conn.execute(
                "UPDATE investigation_jobs "
                "SET status='completed', finished_at=?, error_detail=NULL, updated_at=?, lease_token=NULL, lease_expires_at=NULL "
                "WHERE id=? AND status='running' AND lease_token=?",
                (now, now, job_id, lease_token),
            )
            if finished.rowcount != 1:
                raise RuntimeError("Processing lease was lost before evidence commit")
            db.emit_case_event(conn, request.case_id, "job_changed")
        if sightings:
            db.emit_case_event(conn, request.case_id, "finding_changed")
    return {
        "case_id": request.case_id,
        "worker_id": request.worker_id,
        "jurisdiction": request.jurisdiction,
        "video_source_id": request.video_source_id,
        "video_sha256": video["sha256"],
        "analysis_engine": "ultralytics-yolo-visible-target-tracking",
        "target_scope": "people and common vehicles/objects; one-clip tracking only",
        "facial_identification": "not performed",
        "cross_camera_identity_matching": "not performed",
        "candidate_sightings": sightings,
        "follow_up_jobs": [],
        "human_review_required": True,
    }


@app.get("/api/v1/sightings")
def list_sightings(case_id: str | None = None, review_status: str | None = None,
                   all_detections: bool = False, user: AuthenticatedUser = Depends(require_approved_officer)):
    db.initialize()
    query = (
        "SELECT s.id,s.case_id,s.job_id,s.worker_id,s.video_source_id,s.occurred_at,s.video_offset_seconds,"
        "s.track_id,s.vehicle_type,s.vehicle_color,s.subject_type,s.visual_attributes_json,"
        "s.clue_json,s.frame_path,s.frame_storage_key,s.frame_sha256,s.detector_confidence,s.clue_match_score,"
        "s.evidence_sha256,s.review_status,s.reviewer_user_id,s.reviewed_at,s.review_note,s.created_at,"
        "c.clues_json AS case_clues_json "
        "FROM sightings s JOIN cases c ON c.case_id=s.case_id WHERE c.owner_id=?"
    )
    params: list[str] = [user.user_id]
    if case_id:
        query += " AND s.case_id=?"
        params.append(case_id)
    if review_status:
        query += " AND s.review_status=?"
        params.append(review_status)
    if not all_detections:
        query += " AND (s.clue_match_score >= 0.99 OR s.review_status='confirmed_candidate')"
    query += " ORDER BY s.occurred_at DESC"
    with db.connection() as conn:
        rows = conn.execute(query, params).fetchall()
    records = []
    for row in rows:
        record = dict(row)
        clue_text = record.pop("case_clues_json", None)
        frame_path = record.pop("frame_path", None)
        frame_storage_key = record.pop("frame_storage_key", None)
        sighting_clue = record.pop("clue_json", None)
        record["clue"] = json.loads(sighting_clue) if sighting_clue else (json.loads(clue_text) if clue_text else None)
        raw_attrs = record.pop("visual_attributes_json", None)
        attrs = {}
        if raw_attrs:
            try:
                attrs = json.loads(raw_attrs)
            except Exception:
                attrs = {}
        if isinstance(attrs, dict):
            record["visual_attributes"] = attrs.get("tags", [])
            record["bbox"] = attrs.get("bbox")
            record["scaled_bbox"] = attrs.get("scaled_bbox")
            record["frame_width"] = attrs.get("frame_width")
            record["frame_height"] = attrs.get("frame_height")
            record["saved_frame_width"] = attrs.get("saved_frame_width")
            record["saved_frame_height"] = attrs.get("saved_frame_height")
        elif isinstance(attrs, list):
            record["visual_attributes"] = attrs
            record["bbox"] = None
            record["scaled_bbox"] = None
            record["frame_width"] = None
            record["frame_height"] = None
            record["saved_frame_width"] = None
            record["saved_frame_height"] = None
        else:
            record["visual_attributes"] = []
            record["bbox"] = None
            record["scaled_bbox"] = None
            record["frame_width"] = None
            record["frame_height"] = None
            record["saved_frame_width"] = None
            record["saved_frame_height"] = None
        record["subject_type"] = record["subject_type"] or record["vehicle_type"]
        has_frame = bool(frame_path or frame_storage_key)
        record["has_frame"] = has_frame
        record["frame_url"] = f"/api/v1/sightings/{record['id']}/frame" if has_frame else None
        record["annotated_frame_url"] = f"/api/v1/sightings/{record['id']}/frame?annotated=true" if has_frame else None
        records.append(record)
    return records


@app.post("/api/v1/sightings/{sighting_id}/review")
def review_sighting(sighting_id: str, decision: ReviewDecision, user: AuthenticatedUser = Depends(require_approved_officer)):
    db.initialize()
    now = db.utc_now()
    norm_decision = decision.decision.strip().lower()
    if norm_decision in ("not_relevant", "rejected_candidate", "dismissed"):
        norm_decision = "not_relevant"
    elif norm_decision in ("confirmed_candidate", "confirmed"):
        norm_decision = "confirmed_candidate"
    else:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid review decision '{decision.decision}'. Must be 'confirmed_candidate' or 'not_relevant'",
        )

    request_key = (decision.request_key or f"rev_{sighting_id}_{uuid.uuid4().hex[:12]}").strip()
    with db.connection() as conn:
        row = conn.execute(
            "SELECT s.id, s.case_id, s.review_status FROM sightings s JOIN cases c ON c.case_id=s.case_id "
            "WHERE s.id=? AND c.owner_id=?",
            (sighting_id, user.user_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Candidate not found")

        # Idempotency check on finding_reviews (sighting_id, request_key)
        existing = conn.execute(
            "SELECT id, decision, note FROM finding_reviews WHERE sighting_id=? AND request_key=?",
            (sighting_id, request_key),
        ).fetchone()
        if existing:
            if existing["decision"] != norm_decision:
                raise HTTPException(
                    status_code=409,
                    detail=f"Idempotency conflict: review request_key '{request_key}' was already processed with decision '{existing['decision']}'",
                )
            return {
                "sighting_id": sighting_id,
                "review_status": existing["decision"],
                "reviewer_user_id": user.user_id,
                "reviewed_at": now,
                "idempotent": True,
            }

        review_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO finding_reviews(id, sighting_id, case_id, reviewer_user_id, decision, note, request_key, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (review_id, sighting_id, row["case_id"], user.user_id, norm_decision, decision.note, request_key, now),
        )
        conn.execute(
            "UPDATE sightings SET review_status=?, reviewer=?, reviewer_user_id=?, reviewed_at=?, review_note=? WHERE id=?",
            (norm_decision, user.user_id, user.user_id, now, decision.note, sighting_id),
        )
        db.audit(
            conn,
            "human_review_recorded",
            {
                "review_id": review_id,
                "sighting_id": sighting_id,
                "case_id": row["case_id"],
                "decision": norm_decision,
                "reviewer": user.user_id,
                "note": decision.note,
                "request_key": request_key,
            },
        )
        db.emit_case_event(conn, row["case_id"], "review_changed")
        db.emit_case_event(conn, row["case_id"], "finding_changed")
    return {"sighting_id": sighting_id, "review_status": norm_decision, "reviewer_user_id": user.user_id, "reviewed_at": now}


@app.get("/api/v1/sightings/{sighting_id}/reviews")
def review_history(sighting_id: str, user: AuthenticatedUser = Depends(require_approved_officer)):
    db.initialize()
    with db.connection() as conn:
        owned = conn.execute(
            "SELECT s.id, s.case_id FROM sightings s JOIN cases c ON c.case_id=s.case_id WHERE s.id=? AND c.owner_id=?",
            (sighting_id, user.user_id),
        ).fetchone()
        if not owned:
            raise HTTPException(status_code=404, detail="Sighting not found")
        rows = conn.execute(
            "SELECT id, sighting_id, case_id, reviewer_user_id, decision, note, request_key, created_at "
            "FROM finding_reviews WHERE sighting_id=? ORDER BY created_at DESC",
            (sighting_id,),
        ).fetchall()
        if rows:
            return [
                {
                    "id": r["id"],
                    "sighting_id": r["sighting_id"],
                    "reviewer": r["reviewer_user_id"],
                    "decision": r["decision"],
                    "note": r["note"] or "",
                    "request_key": r["request_key"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ]
        # Legacy audit log fallback
        events = conn.execute(
            "SELECT payload_json, created_at FROM audit_log WHERE event_type=? ORDER BY id DESC",
            ("human_review_recorded",),
        ).fetchall()
    history = []
    for event in events:
        try:
            payload = json.loads(event["payload_json"])
            if payload.get("sighting_id") == sighting_id:
                history.append({
                    "id": payload.get("review_id"),
                    "sighting_id": sighting_id,
                    "reviewer": payload.get("reviewer"),
                    "decision": payload.get("decision"),
                    "note": payload.get("note") or "",
                    "request_key": payload.get("request_key"),
                    "created_at": event["created_at"],
                })
        except Exception:
            pass
    return sorted(history, key=lambda item: item["created_at"], reverse=True)


@app.get("/api/v1/followups")
def list_followups(case_id: str | None = None, status: str | None = None, user: AuthenticatedUser = Depends(require_approved_officer)):
    _legacy_disabled()


@app.get("/api/v1/followups/ready")
def ready_followups(case_id: str | None = None, user: AuthenticatedUser = Depends(require_approved_officer)):
    _legacy_disabled()


@app.post("/api/v1/followups/{followup_id}/analyze")
def analyze_followup(followup_id: str, user: AuthenticatedUser = Depends(require_approved_officer)):
    _legacy_disabled()


@app.get("/api/v1/integrity")
def integrity_status(user: AuthenticatedUser = Depends(require_approved_officer)):
    _legacy_disabled()

