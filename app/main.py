from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import logging
import mimetypes
import os
import secrets
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import db
from .auth import AuthenticatedUser, owned_case_or_404, require_approved_officer
from .ollama_client import OLLAMA_BASE_URL, parse_vehicle_clues
from .schemas import AnalyzeZoneRequest, AnalyzeZonesRequest, CameraCreate, InvestigationStatusUpdate, NVRReplayCreate, RecordingCreate, CaseCreate, ClueParseRequest, ResolveWorkItemsRequest, ReviewDecision
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
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
    )
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "1024")) * 1024 * 1024
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


@app.post("/api/v1/cases", status_code=201)
def create_case(request: CaseCreate, user: AuthenticatedUser = Depends(require_approved_officer)):
    db.initialize()
    now = db.utc_now()
    with db.connection() as conn:
        existing = conn.execute("SELECT case_id FROM cases WHERE case_id=?", (request.case_id,)).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="case_id already exists")
        conn.execute(
            "INSERT INTO cases(case_id,owner_id,description,initial_zone,neighbors_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            (request.case_id, user.user_id, request.description, request.initial_zone, db.canonical_json(request.neighbors), now, now),
        )
        db.audit(conn, "case_created", {"case_id": request.case_id, "owner_id": user.user_id})
    return {"case_id": request.case_id, "status": "draft", "neighbors": request.neighbors, "created_at": now}


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
    with db.connection() as conn:
        row = dict(owned_case_or_404(conn, case_id, user.user_id))
        state = conn.execute("SELECT status FROM investigation_states WHERE case_id=?", (case_id,)).fetchone()
    return {
        "case_id": row["case_id"],
        "title": row.get("title"),
        "description": row["description"],
        "status": state["status"] if state else "draft",
        "clues": json.loads(row["clues_json"]) if row.get("clues_json") else None,
        "authorized_at": row.get("authorized_at"),
        "created_at": row["created_at"],
        "updated_at": row.get("updated_at"),
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
    with db.connection() as conn:
        owned_case_or_404(conn, case_id, user.user_id)
    try:
        from .orchestration import plan_investigation
        return await plan_investigation(case_id, idempotency_key)
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
    with db.connection() as conn:
        owned_case_or_404(conn, case_id, user.user_id)
        jobs = [
            {
                "id": row["id"],
                "video_source_id": row["video_source_id"],
                "worker_id": row["worker_id"],
                "status": row["status"],
                "attempts": int(row["attempts"] or 0),
                "updated_at": row["updated_at"],
            }
            for row in conn.execute(
                "SELECT id,video_source_id,worker_id,status,attempts,updated_at FROM investigation_jobs WHERE case_id=? ORDER BY created_at",
                (case_id,),
            ).fetchall()
        ]
        count = conn.execute("SELECT COUNT(*) AS total FROM sightings WHERE case_id=?", (case_id,)).fetchone()["total"]
        state = conn.execute("SELECT status,updated_at FROM investigation_states WHERE case_id=?", (case_id,)).fetchone()
    return {
        "case_id": case_id,
        "status": state["status"] if state else "draft",
        "status_updated_at": state["updated_at"] if state else None,
        "jobs": jobs,
        "sighting_count": count,
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
        if previous_clue_json:
            conn.execute("UPDATE sightings SET clue_json=? WHERE case_id=? AND clue_json IS NULL",
                         (previous_clue_json, request.case_id))
        conn.execute("UPDATE cases SET description=?,clues_json=?,updated_at=? WHERE case_id=? AND owner_id=?",
                     (request.description, clue.model_dump_json(), db.utc_now(), request.case_id, user.user_id))
        db.audit(conn, "case_clue_updated", {"case_id": request.case_id, "owner_id": user.user_id,
                                               "parser": clue.parser, "clue": clue.model_dump()})
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
    jurisdiction: str = Form(...),
    camera_id: str = Form(...),
    recorded_at: str = Form(...),
    file: UploadFile = File(...),
):
    db.initialize()
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail=f"Supported video extensions: {', '.join(sorted(ALLOWED_EXTENSIONS))}")
    try:
        recorded = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="recorded_at must be an ISO-8601 timestamp") from exc
    with db.connection() as conn:
        case = owned_case_or_404(conn, case_id, user.user_id)
        if jurisdiction not in _case_zones(case):
            raise HTTPException(status_code=422, detail="Jurisdiction is not part of this case's zone map")
    video_id = str(uuid.uuid4())
    dest = db.EVIDENCE_DIR / f"{video_id}{suffix}"
    digest = hashlib.sha256()
    total = 0
    try:
        with dest.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="Video exceeds configured upload limit")
                digest.update(chunk)
                out.write(chunk)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    sha = digest.hexdigest()
    try:
        import cv2
    except ImportError as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail="Install requirements-video.txt to accept video files") from exc
    capture = cv2.VideoCapture(str(dest))
    if not capture.isOpened():
        capture.release()
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="Uploaded file could not be opened as a video")
    fps = capture.get(cv2.CAP_PROP_FPS) or 0
    frame_count = capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    duration_seconds = float(frame_count / fps) if fps > 0 else 0.0
    capture.release()
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO videos(video_source_id,case_id,jurisdiction,camera_id,original_name,file_path,recorded_at,sha256,byte_size,duration_seconds,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (video_id, case_id, jurisdiction, camera_id, Path(file.filename or "video").name, str(dest), _iso(recorded), sha, total, duration_seconds, db.utc_now()),
        )
        conn.execute("INSERT INTO recordings(id,case_id,camera_id,jurisdiction,source_type,video_source_id,recorded_at,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                     (video_id, case_id, camera_id, jurisdiction, "upload", video_id, _iso(recorded), "ready", db.utc_now()))
        attached_followups = _attach_waiting_followups(conn, case_id, jurisdiction)
        db.audit(conn, "video_registered", {"video_source_id": video_id, "case_id": case_id, "jurisdiction": jurisdiction, "camera_id": camera_id, "sha256": sha, "byte_size": total, "duration_seconds": duration_seconds})
        if attached_followups:
            db.audit(conn, "followup_video_attached", {"video_source_id": video_id, "followup_ids": attached_followups})
    return {"video_source_id": video_id, "case_id": case_id, "jurisdiction": jurisdiction, "camera_id": camera_id, "recorded_at": _iso(recorded), "sha256": sha, "byte_size": total, "duration_seconds": duration_seconds, "status": "stored_locally"}


@app.get("/api/v1/videos")
def list_videos(case_id: str | None = None, jurisdiction: str | None = None, user: AuthenticatedUser = Depends(require_approved_officer)):
    db.initialize()
    query = (
        "SELECT v.video_source_id,v.case_id,v.jurisdiction,v.camera_id,v.original_name,"
        "v.recorded_at,v.sha256,v.byte_size,v.duration_seconds,v.created_at "
        "FROM videos v JOIN cases c ON c.case_id=v.case_id WHERE c.owner_id=?"
    )
    params: list[str] = [user.user_id]
    if case_id:
        query += " AND v.case_id=?"
        params.append(case_id)
    if jurisdiction:
        query += " AND v.jurisdiction=?"
        params.append(jurisdiction)
    query += " ORDER BY v.recorded_at"
    with db.connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


@app.post("/api/v1/work-items/resolve")
def resolve_work_items(request: ResolveWorkItemsRequest, user: AuthenticatedUser = Depends(require_approved_officer)):
    _legacy_disabled()


@app.get("/api/v1/videos/{video_source_id}/file")
def get_video_file(video_source_id: str, user: AuthenticatedUser = Depends(require_approved_officer)):
    db.initialize()
    with db.connection() as conn:
        row = conn.execute(
            "SELECT v.file_path,v.original_name FROM videos v JOIN cases c ON c.case_id=v.case_id "
            "WHERE v.video_source_id=? AND c.owner_id=?",
            (video_source_id, user.user_id),
        ).fetchone()
    if row is None or not Path(row["file_path"]).is_file():
        raise HTTPException(status_code=404, detail="Video not found")
    return FileResponse(
        row["file_path"],
        media_type=mimetypes.guess_type(row["original_name"])[0] or "video/mp4",
        headers={"Content-Disposition": "inline"},
    )


@app.get("/api/v1/sightings/{sighting_id}/frame")
def get_sighting_frame(sighting_id: str, user: AuthenticatedUser = Depends(require_approved_officer)):
    db.initialize()
    with db.connection() as conn:
        row = conn.execute(
            "SELECT s.frame_path FROM sightings s JOIN cases c ON c.case_id=s.case_id "
            "WHERE s.id=? AND c.owner_id=?",
            (sighting_id, user.user_id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    if not row["frame_path"] or not Path(row["frame_path"]).is_file():
        raise HTTPException(status_code=404, detail="No still frame was captured for this stored candidate; rerun the clip search to create one")
    return FileResponse(row["frame_path"], media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@app.post("/api/v1/zones/analyze")
async def analyze_zones(request: AnalyzeZonesRequest, user: AuthenticatedUser = Depends(require_approved_officer)):
    _legacy_disabled()


@app.post("/api/v1/zone/analyze")
def analyze_zone(request: AnalyzeZoneRequest, user: AuthenticatedUser = Depends(require_approved_officer)):
    _legacy_disabled()


def _analyze_zone(request: AnalyzeZoneRequest, job_id: str | None = None, lease_token: str | None = None):
    db.initialize()
    with db.connection() as conn:
        case = _case_or_404(conn, request.case_id)
        if request.jurisdiction not in _case_zones(case):
            raise HTTPException(status_code=422, detail="Jurisdiction is not part of this case's zone map")
        video = conn.execute("SELECT * FROM videos WHERE video_source_id=?", (request.video_source_id,)).fetchone()
        if video is None:
            raise HTTPException(status_code=404, detail="video_source_id is not registered in the local evidence store")
        if video["case_id"] != request.case_id or video["jurisdiction"] != request.jurisdiction:
            raise HTTPException(status_code=409, detail="Video case or jurisdiction does not match this worker assignment")
        clue = request.clue.model_dump() if request.clue else (json.loads(case["clues_json"]) if case["clues_json"] else None)
    try:
        detections = analyze_video(video["file_path"], datetime.fromisoformat(video["recorded_at"]), float(os.getenv("YOLO_CONFIDENCE", "0.30")))
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="OpenCV/Ultralytics are not installed; install requirements and configure YOLO") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Video analysis failed: {type(exc).__name__}: {exc}") from exc

    sightings: list[dict[str, Any]] = []
    followups: list[dict[str, Any]] = []
    neighbor_map = json.loads(case["neighbors_json"])
    neighbor_zones = list(dict.fromkeys([*request.neighbors, *neighbor_map.get(request.jurisdiction, [])]))
    with db.connection() as conn:
        for detection in detections:
            score = _match_score(detection, clue)
            # Keep only actual type/color matches. Free-text marks/accessories remain for
            # human review and are never claimed as machine-confirmed attributes.
            if not _matches_machine_clue(detection, clue):
                continue
            # Persist a normalized match indicator: every machine-readable type/color
            # component passed. This is a clue filter, not a probability of identity.
            score = 1.0
            sighting_id = str(uuid.uuid4())
            subject_type = detection.get("subject_type") or detection.get("vehicle_type") or "vehicle"
            visual_attributes = detection.get("visual_attributes") or []
            frame_bytes = detection.get("frame_jpeg")
            frame_offset = float(detection.get("frame_offset_seconds", detection["start_offset_seconds"]))
            occurred = _iso(datetime.fromisoformat(video["recorded_at"]) + timedelta(seconds=frame_offset))
            frame_path = None
            frame_sha256 = None
            if frame_bytes:
                frame_dir = db.EVIDENCE_DIR / "frames"
                frame_dir.mkdir(parents=True, exist_ok=True)
                frame_path = frame_dir / f"{sighting_id}.jpg"
                frame_path.write_bytes(frame_bytes)
                frame_sha256 = hashlib.sha256(frame_bytes).hexdigest()
            integrity_payload = {
                "video_sha256": video["sha256"], "case_id": request.case_id, "worker_id": request.worker_id,
                "jurisdiction": request.jurisdiction, "camera_id": video["camera_id"], "occurred_at": occurred,
                "video_offset_seconds": frame_offset, "vehicle_type": detection["vehicle_type"],
                "vehicle_color": detection.get("vehicle_color"), "detector_confidence": detection["detector_confidence"],
                "subject_type": subject_type, "visual_attributes": visual_attributes,
                "frame_sha256": frame_sha256,
                "clue": clue,
            }
            evidence_hash = hashlib.sha256((video["sha256"] + db.canonical_json(integrity_payload)).encode("utf-8")).hexdigest()
            conn.execute(
                "INSERT INTO sightings(id,case_id,worker_id,jurisdiction,camera_id,video_source_id,occurred_at,video_offset_seconds,track_id,vehicle_type,vehicle_color,subject_type,visual_attributes_json,integrity_version,clue_json,frame_path,frame_sha256,detector_confidence,clue_match_score,evidence_sha256,review_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sighting_id, request.case_id, request.worker_id, request.jurisdiction, video["camera_id"], request.video_source_id,
                 occurred, frame_offset, detection.get("track_id"), detection["vehicle_type"], detection.get("vehicle_color"),
                 subject_type, db.canonical_json(visual_attributes), 4, db.canonical_json(clue) if clue else None,
                 str(frame_path) if frame_path else None, frame_sha256,
                 detection["detector_confidence"], score, evidence_hash, "pending", db.utc_now()),
            )
            sighting = {"id": sighting_id, "case_id": request.case_id, "worker_id": request.worker_id, "jurisdiction": request.jurisdiction,
                        "camera_id": video["camera_id"], "video_source_id": request.video_source_id, "occurred_at": occurred,
                        "video_offset_seconds": frame_offset, "track_id": detection.get("track_id"),
                        "vehicle_type": detection["vehicle_type"], "vehicle_color": detection.get("vehicle_color"),
                        "subject_type": subject_type, "visual_attributes": visual_attributes,
                        "attributes_require_human_review": True,
                        "detector_confidence": detection["detector_confidence"], "clue_match_score": score,
                        "frame_url": f"/api/v1/sightings/{sighting_id}/frame" if frame_path else None,
                        "frame_sha256": frame_sha256,
                        "evidence_sha256": evidence_hash, "review_status": "pending"}
            sightings.append(sighting)
            # Do not link or propagate a person candidate across cameras/zones.
            if subject_type != "person" and score is not None and score >= FOLLOWUP_THRESHOLD:
                follow_start = datetime.fromisoformat(detection["last_seen_at"].isoformat())
                follow_end = follow_start + timedelta(minutes=10)
                for zone in neighbor_zones:
                    available = _matching_video_for_window(conn, request.case_id, zone, follow_start, follow_end)
                    job_id = str(uuid.uuid4())
                    conn.execute(
                        "INSERT OR IGNORE INTO followups(id,case_id,source_sighting_id,target_zone,window_start,window_end,video_source_id,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                        (job_id, request.case_id, sighting_id, zone, _iso(follow_start), _iso(follow_end), available["video_source_id"] if available else None,
                         "pending" if available else "awaiting_video", db.utc_now()),
                    )
        for row in conn.execute("SELECT * FROM followups WHERE case_id=? AND source_sighting_id IN (SELECT id FROM sightings WHERE video_source_id=?)", (request.case_id, request.video_source_id)).fetchall():
            followups.append(dict(row))
        db.audit(conn, "zone_analysis_completed", {"case_id": request.case_id, "worker_id": request.worker_id, "jurisdiction": request.jurisdiction,
                                                     "video_source_id": request.video_source_id, "sighting_count": len(sightings), "followup_count": len(followups)})
        if job_id:
            finished = conn.execute("UPDATE investigation_jobs SET status='completed',updated_at=?,lease_token=NULL,lease_expires_at=NULL WHERE id=? AND status='running' AND lease_token=?",
                                    (db.utc_now(), job_id, lease_token))
            if finished.rowcount != 1:
                raise RuntimeError("Processing lease was lost before evidence commit")
    return {"case_id": request.case_id, "worker_id": request.worker_id, "jurisdiction": request.jurisdiction,
            "video_source_id": request.video_source_id, "video_sha256": video["sha256"], "analysis_engine": "ultralytics-yolo-visible-target-tracking",
            "target_scope": "people and common vehicles/objects; one-clip tracking only",
            "facial_identification": "not performed", "cross_camera_identity_matching": "not performed",
            "candidate_sightings": sightings, "follow_up_jobs": followups,
            "human_review_required": True}


@app.get("/api/v1/sightings")
def list_sightings(case_id: str | None = None, review_status: str | None = None,
                   all_detections: bool = False, user: AuthenticatedUser = Depends(require_approved_officer)):
    db.initialize()
    query = (
        "SELECT s.id,s.case_id,s.job_id,s.worker_id,s.video_source_id,s.occurred_at,s.video_offset_seconds,"
        "s.track_id,s.vehicle_type,s.vehicle_color,s.subject_type,s.visual_attributes_json,"
        "s.clue_json,s.frame_path,s.frame_sha256,s.detector_confidence,s.clue_match_score,"
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
        clue_text = record.pop("case_clues_json")
        frame_path = record.pop("frame_path", None)
        sighting_clue = record.pop("clue_json", None)
        record["clue"] = json.loads(sighting_clue) if sighting_clue else (json.loads(clue_text) if clue_text else None)
        record["visual_attributes"] = json.loads(record.pop("visual_attributes_json") or "[]")
        record["subject_type"] = record["subject_type"] or record["vehicle_type"]
        record["frame_url"] = f"/api/v1/sightings/{record['id']}/frame" if frame_path else None
        records.append(record)
    return records


@app.post("/api/v1/sightings/{sighting_id}/review")
def review_sighting(sighting_id: str, decision: ReviewDecision, user: AuthenticatedUser = Depends(require_approved_officer)):
    db.initialize()
    now = db.utc_now()
    with db.connection() as conn:
        row = conn.execute(
            "SELECT s.id,s.case_id FROM sightings s JOIN cases c ON c.case_id=s.case_id "
            "WHERE s.id=? AND c.owner_id=?",
            (sighting_id, user.user_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Sighting not found")
        conn.execute(
            "UPDATE sightings SET review_status=?,reviewer=?,reviewer_user_id=?,reviewed_at=?,review_note=? WHERE id=?",
            (decision.decision, user.user_id, user.user_id, now, decision.note, sighting_id),
        )
        db.audit(
            conn,
            "human_review_recorded",
            {"sighting_id": sighting_id, "case_id": row["case_id"], "decision": decision.decision, "reviewer": user.user_id, "note": decision.note},
        )
    return {"sighting_id": sighting_id, "review_status": decision.decision, "reviewer_user_id": user.user_id, "reviewed_at": now}


@app.get("/api/v1/sightings/{sighting_id}/reviews")
def review_history(sighting_id: str, user: AuthenticatedUser = Depends(require_approved_officer)):
    with db.connection() as conn:
        owned = conn.execute(
            "SELECT s.id FROM sightings s JOIN cases c ON c.case_id=s.case_id WHERE s.id=? AND c.owner_id=?",
            (sighting_id, user.user_id),
        ).fetchone()
        if not owned:
            raise HTTPException(status_code=404, detail="Sighting not found")
        events = conn.execute("SELECT payload_json,created_at FROM audit_log WHERE event_type=? ORDER BY id DESC",
                              ("human_review_recorded",)).fetchall()
    history = []
    for event in events:
        payload = json.loads(event["payload_json"])
        if payload.get("sighting_id") == sighting_id:
            history.append({"reviewer": payload.get("reviewer"), "decision": payload.get("decision"),
                            "note": payload.get("note"), "created_at": event["created_at"]})
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

