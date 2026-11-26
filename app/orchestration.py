"""LangGraph planning using existing case, video, and job records."""
from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from . import db
from .ollama_client import parse_vehicle_clues


class InvestigationState(TypedDict, total=False):
    run_id: str
    case_id: str
    description: str
    initial_zone: str
    neighbors: dict[str, list[str]]
    clue: dict[str, Any]
    videos: list[dict[str, Any]]
    jobs: list[dict[str, Any]]
    status: str
    review_required: bool
    followups: list[dict[str, Any]]


def _save(state: InvestigationState) -> None:
    with db.connection() as conn:
        conn.execute("UPDATE orchestration_runs SET state_json=?,updated_at=? WHERE id=?",
                     (db.canonical_json(state), db.utc_now(), state["run_id"]))


async def extract_clue(state: InvestigationState) -> dict:
    clue = await parse_vehicle_clues(state["case_id"], state["description"], state["initial_zone"])
    if not clue.visual_targets and not clue.vehicle_type:
        raise ValueError("No supported search target was extracted")
    updated = {**state, "clue": clue.model_dump(), "status": "clue_parsed"}
    _save(updated)
    return {"clue": updated["clue"], "status": updated["status"]}


def select_recordings(state: InvestigationState) -> dict:
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT video_source_id,camera_id,jurisdiction,recorded_at FROM videos WHERE case_id=? ORDER BY recorded_at,video_source_id",
            (state["case_id"],),
        ).fetchall()
    videos = [dict(row) for row in rows if row["jurisdiction"] in state["neighbors"]]
    _save({**state, "videos": videos, "status": "recordings_selected"})
    return {"videos": videos, "status": "recordings_selected"}


def create_jobs(state: InvestigationState) -> dict:
    jobs = []
    with db.connection() as conn:
        for video in state["videos"]:
            existing = conn.execute("SELECT job_id FROM orchestration_tasks WHERE run_id=? AND video_source_id=?",
                                    (state["run_id"], video["video_source_id"])).fetchone()
            if existing:
                job_id = existing["job_id"]
            else:
                job_id = str(uuid.uuid4())
                now = db.utc_now()
                request = {"case_id": state["case_id"], "jurisdiction": video["jurisdiction"],
                           "video_source_id": video["video_source_id"], "worker_id": "zone_worker_" + video["jurisdiction"],
                           "clue": state["clue"], "neighbors": state["neighbors"][video["jurisdiction"]]}
                conn.execute("INSERT INTO investigation_jobs(id,case_id,jurisdiction,video_source_id,worker_id,status,created_at,updated_at,request_json) VALUES(?,?,?,?,?,?,?,?,?)",
                             (job_id, state["case_id"], video["jurisdiction"], video["video_source_id"],
                              "zone_worker_" + video["jurisdiction"], "queued", now, now, db.canonical_json(request)))
                conn.execute("INSERT INTO orchestration_tasks(run_id,video_source_id,job_id) VALUES(?,?,?)",
                             (state["run_id"], video["video_source_id"], job_id))
            jobs.append({"job_id": job_id, **video})
        updated = {**state, "jobs": jobs, "status": "planned", "review_required": True}
        conn.execute("UPDATE orchestration_runs SET state_json=?,updated_at=? WHERE id=?",
                     (db.canonical_json(updated), db.utc_now(), state["run_id"]))
        db.audit(conn, "orchestration_planned", {"run_id": state["run_id"], "job_count": len(jobs)})
    return {"jobs": jobs, "status": "planned", "review_required": True}


def plan_followups(state: InvestigationState) -> dict:
    from .main import _iso, _matching_video_for_window, _parse_timestamp

    proposed = []
    with db.connection() as conn:
        candidates = conn.execute(
            "SELECT id,jurisdiction,occurred_at FROM sightings WHERE case_id=? AND review_status='confirmed_candidate' ORDER BY occurred_at LIMIT 20",
            (state["case_id"],),
        ).fetchall()
        for candidate in candidates:
            start = _parse_timestamp(candidate["occurred_at"])
            end = start + timedelta(minutes=10)
            for zone in list(dict.fromkeys(state["neighbors"].get(candidate["jurisdiction"], [])))[:2]:
                video = _matching_video_for_window(conn, state["case_id"], zone, start, end)
                existing = conn.execute("SELECT id FROM followups WHERE source_sighting_id=? AND target_zone=?",
                                        (candidate["id"], zone)).fetchone()
                if existing:
                    followup_id = existing["id"]
                else:
                    followup_id = str(uuid.uuid4())
                    conn.execute(
                        "INSERT OR IGNORE INTO followups(id,case_id,source_sighting_id,target_zone,window_start,window_end,video_source_id,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                        (followup_id, state["case_id"], candidate["id"], zone, _iso(start), _iso(end),
                         video["video_source_id"] if video else None, "pending" if video else "awaiting_video", db.utc_now()),
                    )
                    saved = conn.execute("SELECT id FROM followups WHERE source_sighting_id=? AND target_zone=?",
                                         (candidate["id"], zone)).fetchone()
                    followup_id = saved["id"]
                proposed.append({"source_sighting_id": candidate["id"], "target_zone": zone, "followup_id": followup_id})
        updated = {**state, "followups": proposed, "status": "planned", "review_required": True}
        conn.execute("UPDATE orchestration_runs SET state_json=?,updated_at=? WHERE id=?",
                     (db.canonical_json(updated), db.utc_now(), state["run_id"]))
    return {"followups": proposed, "status": "planned", "review_required": True}


graph = StateGraph(InvestigationState)
graph.add_node("extract_clue", extract_clue)
graph.add_node("select_recordings", select_recordings)
graph.add_node("create_jobs", create_jobs)
graph.add_node("plan_followups", plan_followups)
graph.add_edge(START, "extract_clue")
graph.add_edge("extract_clue", "select_recordings")
graph.add_edge("select_recordings", "create_jobs")
graph.add_edge("create_jobs", "plan_followups")
graph.add_edge("plan_followups", END)
workflow = graph.compile()


async def plan_investigation(case_id: str, idempotency_key: str | None = None) -> InvestigationState:
    db.initialize()
    with db.connection() as conn:
        case = conn.execute("SELECT * FROM cases WHERE case_id=?", (case_id,)).fetchone()
        if not case:
            raise LookupError("Case not found")
        run_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "cctv:" + case_id + ":" + idempotency_key)) if idempotency_key else str(uuid.uuid4())
        state: InvestigationState = {
            "run_id": run_id, "case_id": case_id, "description": case["description"],
            "initial_zone": case["initial_zone"], "neighbors": json.loads(case["neighbors_json"]), "status": "started",
        }
        now = db.utc_now()
        inserted = conn.execute("INSERT INTO orchestration_runs(id,case_id,state_json,created_at,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
                                (run_id, case_id, db.canonical_json(state), now, now))
        if inserted.rowcount == 0:
            existing = conn.execute("SELECT state_json FROM orchestration_runs WHERE id=?", (run_id,)).fetchone()
            return json.loads(existing["state_json"])
    try:
        return await workflow.ainvoke(state)
    except Exception:
        with db.connection() as conn:
            row = conn.execute("SELECT state_json FROM orchestration_runs WHERE id=?", (run_id,)).fetchone()
            failed = json.loads(row["state_json"])
            failed["status"] = "failed"
            conn.execute("UPDATE orchestration_runs SET state_json=?,updated_at=? WHERE id=?",
                         (db.canonical_json(failed), db.utc_now(), run_id))
        raise
