"""LangGraph central orchestrator using existing case, video, and job records."""
from __future__ import annotations

import json
import uuid
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from . import db
from .ollama_client import parse_vehicle_clues


class InvestigationConflictError(RuntimeError):
    """Raised when an investigation has already been reserved or started."""


class InvestigationState(TypedDict, total=False):
    run_id: str
    case_id: str
    start_key: str | None
    description: str
    initial_zone: str
    neighbors: dict[str, list[str]]
    clue: dict[str, Any]
    videos: list[dict[str, Any]]
    input_video_ids: list[str]
    jobs: list[dict[str, Any]]
    status: str
    review_required: bool
    idempotent: bool


def _save_run(state: InvestigationState, *, status: str | None = None, input_video_ids: list[str] | None = None) -> None:
    now = db.utc_now()
    run_status = status or state.get("status") or "planning"
    with db.connection() as conn:
        if input_video_ids is not None:
            ids_json = db.canonical_json(input_video_ids)
            conn.execute(
                "UPDATE orchestration_runs SET state_json=?, status=?, input_video_ids=?, updated_at=? WHERE id=?",
                (db.canonical_json(state), run_status, ids_json, now, state["run_id"]),
            )
        else:
            conn.execute(
                "UPDATE orchestration_runs SET state_json=?, status=?, updated_at=? WHERE id=?",
                (db.canonical_json(state), run_status, now, state["run_id"]),
            )


async def extract_clue(state: InvestigationState) -> dict:
    clue = await parse_vehicle_clues(state["case_id"], state["description"], state["initial_zone"])
    if not clue.visual_targets and not clue.vehicle_type:
        if clue.unsupported_requests:
            raise ValueError(
                "No supported visual search target was extracted. Unsupported requests: "
                + ", ".join(clue.unsupported_requests)
            )
        raise ValueError("No supported search target was extracted from the investigation description")
    clue_dump = clue.model_dump()
    now = db.utc_now()
    with db.connection() as conn:
        # Persist validated parsed clue separately without overwriting original description.
        conn.execute(
            "UPDATE cases SET clues_json=?, updated_at=? WHERE case_id=?",
            (db.canonical_json(clue_dump), now, state["case_id"]),
        )
        db.audit(
            conn,
            "orchestration_clue_parsed",
            {"run_id": state["run_id"], "case_id": state["case_id"], "parser": clue.parser, "clue": clue_dump},
        )
    updated = {**state, "clue": clue_dump, "status": "planning"}
    _save_run(updated, status="planning")
    return {"clue": clue_dump, "status": "planning"}


def select_recordings(state: InvestigationState) -> dict:
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT video_source_id,camera_id,jurisdiction,recorded_at,sha256 "
            "FROM videos WHERE case_id=? AND media_status='ready' ORDER BY created_at,video_source_id",
            (state["case_id"],),
        ).fetchall()
    videos = [dict(row) for row in rows if row["jurisdiction"] in state["neighbors"]]
    if not videos:
        raise ValueError("Upload at least one ready video before starting the investigation")
    input_video_ids = [v["video_source_id"] for v in videos]
    updated = {**state, "videos": videos, "input_video_ids": input_video_ids, "status": "planning"}
    _save_run(updated, status="planning", input_video_ids=input_video_ids)
    return {"videos": videos, "input_video_ids": input_video_ids, "status": "planning"}


def create_jobs(state: InvestigationState) -> dict:
    jobs: list[dict[str, Any]] = []
    now = db.utc_now()
    with db.connection() as conn:
        db.lock_case_row(conn, state["case_id"])
        for video in state["videos"]:
            existing = conn.execute(
                "SELECT job_id FROM orchestration_tasks WHERE run_id=? AND video_source_id=?",
                (state["run_id"], video["video_source_id"]),
            ).fetchone()
            if existing:
                job_id = existing["job_id"]
            else:
                job_id = str(uuid.uuid4())
                request = {
                    "case_id": state["case_id"],
                    "run_id": state["run_id"],
                    "jurisdiction": video["jurisdiction"],
                    "video_source_id": video["video_source_id"],
                    "worker_id": "pending-worker",
                    "clue": state["clue"],
                    "neighbors": state["neighbors"].get(video["jurisdiction"], []),
                }
                conn.execute(
                    "INSERT INTO investigation_jobs(id,case_id,run_id,jurisdiction,video_source_id,worker_id,status,created_at,updated_at,request_json) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        job_id,
                        state["case_id"],
                        state["run_id"],
                        video["jurisdiction"],
                        video["video_source_id"],
                        "pending-worker",
                        "queued",
                        now,
                        now,
                        db.canonical_json(request),
                    ),
                )
                conn.execute(
                    "INSERT INTO orchestration_tasks(run_id,video_source_id,job_id) VALUES(?,?,?)",
                    (state["run_id"], video["video_source_id"], job_id),
                )
            jobs.append({"job_id": job_id, **video})

        updated = {**state, "jobs": jobs, "status": "queued", "review_required": True}
        conn.execute(
            "UPDATE orchestration_runs SET state_json=?, status='queued', has_jobs=?, started_at=?, updated_at=? WHERE id=?",
            (db.canonical_json(updated), True, now, now, state["run_id"]),
        )
        conn.execute(
            "INSERT INTO investigation_states(case_id,status,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(case_id) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at",
            (state["case_id"], "queued", now),
        )
        db.emit_case_event(conn, state["case_id"], "case_changed")
        db.emit_case_event(conn, state["case_id"], "job_changed")
        db.audit(
            conn,
            "orchestration_planned",
            {
                "run_id": state["run_id"],
                "case_id": state["case_id"],
                "job_count": len(jobs),
                "input_video_ids": state.get("input_video_ids") or [v["video_source_id"] for v in state["videos"]],
            },
        )
    return {"jobs": jobs, "status": "queued", "review_required": True}


graph = StateGraph(InvestigationState)
graph.add_node("extract_clue", extract_clue)
graph.add_node("select_recordings", select_recordings)
graph.add_node("create_jobs", create_jobs)
graph.add_edge(START, "extract_clue")
graph.add_edge("extract_clue", "select_recordings")
graph.add_edge("select_recordings", "create_jobs")
graph.add_edge("create_jobs", END)
workflow = graph.compile()


async def plan_investigation(case_id: str, idempotency_key: str | None = None) -> InvestigationState:
    db.initialize()
    clean_key = (idempotency_key or "").strip() or None
    now = db.utc_now()

    with db.connection() as conn:
        db.lock_case_row(conn, case_id)
        case = conn.execute("SELECT * FROM cases WHERE case_id=?", (case_id,)).fetchone()
        if not case:
            raise LookupError("Case not found")
        if not case["authorized_at"]:
            raise ValueError("Footage authorization confirmation is required before starting")
        description = (case["description"] or "").strip()
        if len(description) < 3:
            raise ValueError("Investigation description must be at least 3 characters")

        ready_count = conn.execute(
            "SELECT COUNT(*) AS total FROM videos WHERE case_id=? AND media_status='ready'",
            (case_id,),
        ).fetchone()["total"]
        if int(ready_count) < 1:
            raise ValueError("Upload at least one ready video before starting the investigation")

        # Check existing non-recoverable run for this case (exactly one MVP run per new case)
        existing_run = conn.execute(
            "SELECT id, start_key, status, has_jobs, state_json FROM orchestration_runs "
            "WHERE case_id=? AND NOT (status = 'failed' AND has_jobs = ?) ORDER BY created_at DESC LIMIT 1",
            (case_id, False),
        ).fetchone()
        if existing_run:
            if clean_key and existing_run["start_key"] == clean_key:
                saved = json.loads(existing_run["state_json"])
                saved["idempotent"] = True
                return saved
            raise InvestigationConflictError("Investigation has already been started")

        # Also check idempotency key on failed runs if replayed
        if clean_key:
            key_run = conn.execute(
                "SELECT id, status, state_json FROM orchestration_runs WHERE case_id=? AND start_key=?",
                (case_id, clean_key),
            ).fetchone()
            if key_run and key_run["status"] != "failed":
                saved = json.loads(key_run["state_json"])
                saved["idempotent"] = True
                return saved

        run_id = (
            str(uuid.uuid5(uuid.NAMESPACE_URL, f"cctv:{case_id}:{clean_key}:{uuid.uuid4().hex[:8]}"))
            if clean_key
            else str(uuid.uuid4())
        )
        neighbors = json.loads(case["neighbors_json"])
        state: InvestigationState = {
            "run_id": run_id,
            "case_id": case_id,
            "start_key": clean_key,
            "description": description,
            "initial_zone": case["initial_zone"],
            "neighbors": neighbors,
            "status": "planning",
        }
        conn.execute(
            "INSERT INTO orchestration_runs(id,case_id,start_key,status,state_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (run_id, case_id, clean_key, "planning", db.canonical_json(state), now, now),
        )
        # Lock inputs immediately by moving case status from draft -> planning
        conn.execute(
            "INSERT INTO investigation_states(case_id,status,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(case_id) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at",
            (case_id, "planning", now),
        )

    try:
        return await workflow.ainvoke(state)
    except Exception as exc:
        fail_now = db.utc_now()
        with db.connection() as conn:
            db.lock_case_row(conn, case_id)
            row = conn.execute("SELECT state_json FROM orchestration_runs WHERE id=?", (run_id,)).fetchone()
            failed = json.loads(row["state_json"]) if row else dict(state)
            failed["status"] = "failed"
            failed["error_code"] = type(exc).__name__
            failed["error_detail"] = str(exc)
            conn.execute(
                "UPDATE orchestration_runs SET state_json=?, status='failed', error_code=?, finished_at=?, updated_at=? WHERE id=?",
                (db.canonical_json(failed), type(exc).__name__, fail_now, fail_now, run_id),
            )
            # Recover case back to draft only if no jobs were created for this run
            job_count = conn.execute("SELECT COUNT(*) AS total FROM investigation_jobs WHERE run_id=?", (run_id,)).fetchone()["total"]
            if int(job_count) == 0:
                conn.execute(
                    "UPDATE investigation_states SET status='draft', updated_at=? WHERE case_id=? AND status='planning'",
                    (fail_now, case_id),
                )
            db.audit(
                conn,
                "orchestration_planning_failed",
                {"run_id": run_id, "case_id": case_id, "error_code": type(exc).__name__, "error": str(exc)},
            )
        raise
