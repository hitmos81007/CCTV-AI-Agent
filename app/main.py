from __future__ import annotations

import hashlib
import importlib.util
import json
import mimetypes
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from . import db
from .ollama_client import OLLAMA_BASE_URL, OLLAMA_MODEL, parse_vehicle_clues
from .schemas import AnalyzeZoneRequest, CaseCreate, ClueParseRequest, ResolveWorkItemsRequest, ReviewDecision
from .video_analysis import analyze_video


app = FastAPI(title="CCTV Investigation Agent Local API", version="0.1.0")
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "1024")) * 1024 * 1024
FOLLOWUP_THRESHOLD = float(os.getenv("FOLLOWUP_MATCH_THRESHOLD", "0.70"))
ALLOWED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}


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
    checks: list[float] = []
    expected_color = (clue.get("vehicle_color") or "").strip().lower()
    if expected_color:
        checks.append(1.0 if expected_color in (detection.get("vehicle_color") or "").lower() else 0.0)
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
    configured_device = os.getenv("YOLO_DEVICE", "0")
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


@app.get("/", include_in_schema=False)
def review_home():
    return FileResponse(Path(__file__).parent / "static" / "review.html")


@app.get("/health")
async def health():
    db.initialize()
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            response = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            ollama_ready = response.is_success
            ollama_models = [m.get("name") for m in response.json().get("models", [])] if response.is_success else []
    except Exception:
        ollama_ready, ollama_models = False, []
    return {
        "status": "ok",
        "ollama_ready": ollama_ready,
        "ollama_model": OLLAMA_MODEL,
        "ollama_available_models": ollama_models,
        "yolo_model": os.getenv("YOLO_MODEL", "yolo11n.pt"),
        "yolo_device": os.getenv("YOLO_DEVICE", "0"),
        "video_runtime": _video_runtime_status(),
        "face_identification": "not implemented",
        "evidence_directory": str(db.EVIDENCE_DIR),
    }


@app.post("/api/v1/investigations/start", status_code=201)
async def start_investigation(request: CaseCreate):
    """Create/reuse a matching case and extract its vehicle clues with local Ollama."""
    db.initialize()
    with db.connection() as conn:
        existing = conn.execute("SELECT * FROM cases WHERE case_id=?", (request.case_id,)).fetchone()
        if existing:
            expected_neighbors = db.canonical_json(request.neighbors)
            if (existing["description"] != request.description
                    or existing["initial_zone"] != request.initial_zone
                    or existing["neighbors_json"] != expected_neighbors):
                raise HTTPException(status_code=409, detail="case_id exists with different case data")
        else:
            conn.execute(
                "INSERT INTO cases(case_id,description,initial_zone,neighbors_json,created_at) VALUES(?,?,?,?,?)",
                (request.case_id, request.description, request.initial_zone, db.canonical_json(request.neighbors), db.utc_now()),
            )
            db.audit(conn, "case_created", request.model_dump())
    try:
        clue = await parse_vehicle_clues(request.case_id, request.description, request.initial_zone)
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise HTTPException(status_code=503, detail=f"Local Ollama is not available at {OLLAMA_BASE_URL}") from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Ollama returned HTTP {exc.response.status_code}") from exc
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail="Ollama response did not match the clue schema") from exc
    with db.connection() as conn:
        conn.execute("UPDATE cases SET clues_json=? WHERE case_id=?", (clue.model_dump_json(), request.case_id))
        db.audit(conn, "vehicle_clues_parsed", {"case_id": request.case_id, "parser": clue.parser, "clue": clue.model_dump()})
        registered_videos = [dict(row) for row in conn.execute(
            "SELECT video_source_id,case_id,jurisdiction,camera_id,recorded_at,sha256,byte_size,duration_seconds "
            "FROM videos WHERE case_id=? ORDER BY jurisdiction,recorded_at,video_source_id",
            (request.case_id,),
        ).fetchall()]
    return {
        "case_id": request.case_id,
        "description": request.description,
        "initial_zone": request.initial_zone,
        "neighbors": request.neighbors,
        "clue": clue.model_dump(),
        "registered_videos": registered_videos,
        "status": "ready_for_zone_review",
    }


@app.post("/api/v1/cases", status_code=201)
def create_case(request: CaseCreate):
    db.initialize()
    with db.connection() as conn:
        existing = conn.execute("SELECT case_id FROM cases WHERE case_id=?", (request.case_id,)).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="case_id already exists")
        conn.execute(
            "INSERT INTO cases(case_id,description,initial_zone,neighbors_json,created_at) VALUES(?,?,?,?,?)",
            (request.case_id, request.description, request.initial_zone, db.canonical_json(request.neighbors), db.utc_now()),
        )
        db.audit(conn, "case_created", request.model_dump())
    return {"case_id": request.case_id, "status": "created", "neighbors": request.neighbors}


@app.get("/api/v1/cases")
def list_cases():
    db.initialize()
    with db.connection() as conn:
        rows = conn.execute("SELECT * FROM cases ORDER BY created_at DESC").fetchall()
    return [{**dict(row), "neighbors": json.loads(row["neighbors_json"]), "clues": json.loads(row["clues_json"]) if row["clues_json"] else None} for row in rows]


@app.post("/api/v1/clues/parse")
async def parse_clues(request: ClueParseRequest):
    db.initialize()
    with db.connection() as conn:
        case = _case_or_404(conn, request.case_id)
    try:
        clue = await parse_vehicle_clues(request.case_id, request.description, request.initial_zone)
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise HTTPException(status_code=503, detail=f"Local Ollama is not available at {OLLAMA_BASE_URL}") from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Ollama returned HTTP {exc.response.status_code}") from exc
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail="Ollama response did not match the clue schema") from exc
    with db.connection() as conn:
        conn.execute("UPDATE cases SET clues_json=? WHERE case_id=?", (clue.model_dump_json(), request.case_id))
        db.audit(conn, "vehicle_clues_parsed", {"case_id": request.case_id, "parser": clue.parser, "clue": clue.model_dump()})
    return clue.model_dump()


@app.post("/api/v1/videos", status_code=201)
async def upload_video(
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
        case = _case_or_404(conn, case_id)
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
        attached_followups = _attach_waiting_followups(conn, case_id, jurisdiction)
        db.audit(conn, "video_registered", {"video_source_id": video_id, "case_id": case_id, "jurisdiction": jurisdiction, "camera_id": camera_id, "sha256": sha, "byte_size": total, "duration_seconds": duration_seconds})
        if attached_followups:
            db.audit(conn, "followup_video_attached", {"video_source_id": video_id, "followup_ids": attached_followups})
    return {"video_source_id": video_id, "case_id": case_id, "jurisdiction": jurisdiction, "camera_id": camera_id, "recorded_at": _iso(recorded), "sha256": sha, "byte_size": total, "duration_seconds": duration_seconds, "status": "stored_locally"}


@app.get("/api/v1/videos")
def list_videos(case_id: str | None = None, jurisdiction: str | None = None):
    db.initialize()
    query = "SELECT video_source_id,case_id,jurisdiction,camera_id,original_name,recorded_at,sha256,byte_size,duration_seconds,created_at FROM videos WHERE 1=1"
    params: list[str] = []
    if case_id:
        query += " AND case_id=?"
        params.append(case_id)
    if jurisdiction:
        query += " AND jurisdiction=?"
        params.append(jurisdiction)
    query += " ORDER BY recorded_at"
    with db.connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


@app.post("/api/v1/work-items/resolve")
def resolve_work_items(request: ResolveWorkItemsRequest):
    """Attach every locally registered clip for each allowed jurisdiction work item.

    Items without a clip are kept in the response with a null source ID so the
    n8n guard can visibly hold them. No detection or sighting is synthesized.
    """
    db.initialize()
    resolved: list[dict[str, Any]] = []
    with db.connection() as conn:
        case = _case_or_404(conn, request.case_id)
        allowed_zones = _case_zones(case)

        for item in request.items:
            if item.get("case_id") not in (None, request.case_id):
                raise HTTPException(status_code=409, detail="Work item case_id does not match the request")
            zone = str(item.get("jurisdiction") or "").strip()
            if not zone or zone not in allowed_zones:
                raise HTTPException(status_code=422, detail=f"Jurisdiction is not part of this case's zone map: {zone or '(empty)'}")

            requested_video_id = item.get("video_source_id")
            if requested_video_id:
                video_rows = conn.execute(
                    "SELECT video_source_id,case_id,jurisdiction,camera_id,recorded_at,sha256,duration_seconds "
                    "FROM videos WHERE video_source_id=?",
                    (str(requested_video_id),),
                ).fetchall()
                if not video_rows:
                    raise HTTPException(status_code=409, detail="Work item video_source_id is not registered locally")
                if video_rows[0]["case_id"] != request.case_id or video_rows[0]["jurisdiction"] != zone:
                    raise HTTPException(status_code=409, detail="Video case or jurisdiction does not match this work item")
            else:
                video_rows = conn.execute(
                    "SELECT video_source_id,case_id,jurisdiction,camera_id,recorded_at,sha256,duration_seconds "
                    "FROM videos WHERE case_id=? AND jurisdiction=? ORDER BY recorded_at,created_at,video_source_id",
                    (request.case_id, zone),
                ).fetchall()

            if not video_rows:
                resolved.append({
                    **item,
                    "case_id": request.case_id,
                    "video_source_id": None,
                    "status": "awaiting_local_video",
                })
                continue

            for video in video_rows:
                resolved.append({
                    **item,
                    "case_id": request.case_id,
                    "video_source_id": video["video_source_id"],
                    "camera_id": video["camera_id"],
                    "video_recorded_at": video["recorded_at"],
                    "video_sha256": video["sha256"],
                    "video_duration_seconds": video["duration_seconds"],
                    "status": "ready_for_analysis",
                })

    return {
        "case_id": request.case_id,
        "count": len(resolved),
        "ready_count": sum(1 for item in resolved if item["video_source_id"]),
        "items": resolved,
    }


@app.get("/api/v1/videos/{video_source_id}/file")
def get_video_file(video_source_id: str):
    db.initialize()
    with db.connection() as conn:
        row = conn.execute("SELECT file_path,original_name FROM videos WHERE video_source_id=?", (video_source_id,)).fetchone()
    if row is None or not Path(row["file_path"]).is_file():
        raise HTTPException(status_code=404, detail="Video not found")
    return FileResponse(
        row["file_path"],
        media_type=mimetypes.guess_type(row["original_name"])[0] or "video/mp4",
        headers={"Content-Disposition": "inline"},
    )


@app.post("/api/v1/zone/analyze")
def analyze_zone(request: AnalyzeZoneRequest):
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
            sighting_id = str(uuid.uuid4())
            occurred = detection["first_seen_at"]
            if isinstance(occurred, datetime):
                occurred = _iso(occurred)
            integrity_payload = {
                "video_sha256": video["sha256"], "case_id": request.case_id, "worker_id": request.worker_id,
                "jurisdiction": request.jurisdiction, "camera_id": video["camera_id"], "occurred_at": occurred,
                "video_offset_seconds": detection["start_offset_seconds"], "vehicle_type": detection["vehicle_type"],
                "vehicle_color": detection.get("vehicle_color"), "detector_confidence": detection["detector_confidence"],
            }
            evidence_hash = hashlib.sha256((video["sha256"] + db.canonical_json(integrity_payload)).encode("utf-8")).hexdigest()
            conn.execute(
                "INSERT INTO sightings(id,case_id,worker_id,jurisdiction,camera_id,video_source_id,occurred_at,video_offset_seconds,track_id,vehicle_type,vehicle_color,detector_confidence,clue_match_score,evidence_sha256,review_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sighting_id, request.case_id, request.worker_id, request.jurisdiction, video["camera_id"], request.video_source_id,
                 occurred, detection["start_offset_seconds"], detection.get("track_id"), detection["vehicle_type"], detection.get("vehicle_color"),
                 detection["detector_confidence"], score, evidence_hash, "pending", db.utc_now()),
            )
            sighting = {"id": sighting_id, "case_id": request.case_id, "worker_id": request.worker_id, "jurisdiction": request.jurisdiction,
                        "camera_id": video["camera_id"], "video_source_id": request.video_source_id, "occurred_at": occurred,
                        "video_offset_seconds": detection["start_offset_seconds"], "track_id": detection.get("track_id"),
                        "vehicle_type": detection["vehicle_type"], "vehicle_color": detection.get("vehicle_color"),
                        "detector_confidence": detection["detector_confidence"], "clue_match_score": score,
                        "evidence_sha256": evidence_hash, "review_status": "pending"}
            sightings.append(sighting)
            if score is not None and score >= FOLLOWUP_THRESHOLD:
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
    return {"case_id": request.case_id, "worker_id": request.worker_id, "jurisdiction": request.jurisdiction,
            "video_source_id": request.video_source_id, "video_sha256": video["sha256"], "analysis_engine": "ultralytics-yolo-vehicle-tracking",
            "facial_identification": "not performed", "candidate_sightings": sightings, "follow_up_jobs": followups,
            "human_review_required": True}


@app.get("/api/v1/sightings")
def list_sightings(case_id: str | None = None, review_status: str | None = None):
    db.initialize()
    query = "SELECT * FROM sightings WHERE 1=1"
    params: list[str] = []
    if case_id:
        query += " AND case_id=?"
        params.append(case_id)
    if review_status:
        query += " AND review_status=?"
        params.append(review_status)
    query += " ORDER BY occurred_at DESC"
    with db.connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


@app.post("/api/v1/sightings/{sighting_id}/review")
def review_sighting(sighting_id: str, decision: ReviewDecision):
    db.initialize()
    with db.connection() as conn:
        row = conn.execute("SELECT id,case_id,review_status FROM sightings WHERE id=?", (sighting_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Sighting not found")
        conn.execute("UPDATE sightings SET review_status=?,reviewer=?,review_note=? WHERE id=?",
                     (decision.decision, decision.reviewer, decision.note, sighting_id))
        db.audit(conn, "human_review_recorded", {"sighting_id": sighting_id, "case_id": row["case_id"], **decision.model_dump()})
    return {"sighting_id": sighting_id, "review_status": decision.decision, "reviewer": decision.reviewer}


@app.get("/api/v1/followups")
def list_followups(case_id: str | None = None, status: str | None = None):
    db.initialize()
    query = "SELECT * FROM followups WHERE 1=1"
    params: list[str] = []
    if case_id:
        query += " AND case_id=?"
        params.append(case_id)
    if status:
        query += " AND status=?"
        params.append(status)
    query += " ORDER BY created_at"
    with db.connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


@app.get("/api/v1/followups/ready")
def ready_followups(case_id: str | None = None):
    """Return actual, locally registered follow-up work for n8n to fan out to zone workers."""
    db.initialize()
    query = """
        SELECT f.*, c.neighbors_json, c.clues_json, v.camera_id, v.recorded_at, v.sha256
        FROM followups f
        JOIN cases c ON c.case_id=f.case_id
        JOIN videos v ON v.video_source_id=f.video_source_id
        WHERE f.status='pending' AND f.video_source_id IS NOT NULL
    """
    params: list[str] = []
    if case_id:
        query += " AND f.case_id=?"
        params.append(case_id)
    query += " ORDER BY f.created_at"
    with db.connection() as conn:
        rows = conn.execute(query, params).fetchall()
    ready = []
    for row in rows:
        neighbor_map = json.loads(row["neighbors_json"])
        ready.append({
            "followup_id": row["id"], "case_id": row["case_id"],
            "worker_id": f"zone_worker_{row['target_zone'].lower().replace(' ', '_')}",
            "jurisdiction": row["target_zone"], "video_source_id": row["video_source_id"],
            "camera_id": row["camera_id"], "recorded_at": row["recorded_at"],
            "video_sha256": row["sha256"], "window_start": row["window_start"], "window_end": row["window_end"],
            "neighbors": neighbor_map.get(row["target_zone"], []),
            "clue": json.loads(row["clues_json"]) if row["clues_json"] else None,
            "analysis_url": "/api/v1/followups/{followup_id}/analyze".format(followup_id=row["id"]),
        })
    return {"count": len(ready), "items": ready}


@app.post("/api/v1/followups/{followup_id}/analyze")
def analyze_followup(followup_id: str):
    """Claim and analyze a follow-up only when its real local clip is registered."""
    db.initialize()
    with db.connection() as conn:
        row = conn.execute(
            "SELECT f.*,c.neighbors_json,c.clues_json FROM followups f JOIN cases c ON c.case_id=f.case_id WHERE f.id=?",
            (followup_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Follow-up job not found")
        if row["status"] != "pending" or not row["video_source_id"]:
            raise HTTPException(status_code=409, detail="Follow-up is not ready for analysis")
        updated = conn.execute(
            "UPDATE followups SET status='processing' WHERE id=? AND status='pending'",
            (followup_id,),
        )
        if updated.rowcount != 1:
            raise HTTPException(status_code=409, detail="Follow-up is already being processed")
        payload = {
            "case_id": row["case_id"],
            "worker_id": f"zone_worker_{row['target_zone'].lower().replace(' ', '_')}",
            "jurisdiction": row["target_zone"], "video_source_id": row["video_source_id"],
            "neighbors": json.loads(row["neighbors_json"]).get(row["target_zone"], []),
            "clue": json.loads(row["clues_json"]) if row["clues_json"] else None,
        }
    try:
        result = analyze_zone(AnalyzeZoneRequest(**payload))
    except Exception as exc:
        with db.connection() as conn:
            conn.execute("UPDATE followups SET status='failed' WHERE id=? AND status='processing'", (followup_id,))
            db.audit(conn, "followup_analysis_failed", {"followup_id": followup_id, "error_type": type(exc).__name__})
        raise
    with db.connection() as conn:
        conn.execute("UPDATE followups SET status='completed' WHERE id=? AND status='processing'", (followup_id,))
        db.audit(conn, "followup_analysis_completed", {"followup_id": followup_id, "sighting_count": len(result["candidate_sightings"])})
    return {"followup_id": followup_id, **result}


@app.get("/api/v1/integrity")
def integrity_status():
    db.initialize()
    invalid_files: list[str] = []
    missing_files: list[str] = []
    invalid_sightings: list[str] = []
    with db.connection() as conn:
        videos = conn.execute("SELECT video_source_id,file_path,sha256 FROM videos").fetchall()
        for video in videos:
            path = Path(video["file_path"])
            if not path.is_file():
                missing_files.append(video["video_source_id"])
                continue
            digest = hashlib.sha256()
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != video["sha256"]:
                invalid_files.append(video["video_source_id"])
        sightings = conn.execute(
            "SELECT s.*,v.sha256 AS video_sha256 FROM sightings s JOIN videos v ON v.video_source_id=s.video_source_id"
        ).fetchall()
        for sighting in sightings:
            payload = {
                "video_sha256": sighting["video_sha256"], "case_id": sighting["case_id"], "worker_id": sighting["worker_id"],
                "jurisdiction": sighting["jurisdiction"], "camera_id": sighting["camera_id"], "occurred_at": sighting["occurred_at"],
                "video_offset_seconds": sighting["video_offset_seconds"], "vehicle_type": sighting["vehicle_type"],
                "vehicle_color": sighting["vehicle_color"], "detector_confidence": sighting["detector_confidence"],
            }
            expected = hashlib.sha256((sighting["video_sha256"] + db.canonical_json(payload)).encode("utf-8")).hexdigest()
            if expected != sighting["evidence_sha256"]:
                invalid_sightings.append(sighting["id"])
    audit_valid = db.verify_audit_chain()
    files_valid = not invalid_files and not missing_files
    sightings_valid = not invalid_sightings
    return {
        "valid": audit_valid and files_valid and sightings_valid,
        "audit_chain_valid": audit_valid,
        "evidence_files_valid": files_valid,
        "missing_video_ids": missing_files,
        "modified_video_ids": invalid_files,
        "sighting_hashes_valid": sightings_valid,
        "invalid_sighting_ids": invalid_sightings,
    }

