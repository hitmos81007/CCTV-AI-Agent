"""Lease-based durable worker for persisted LangGraph video analysis jobs."""
from __future__ import annotations

import json
import logging
import os
import signal
import socket
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from . import db
from .schemas import AnalyzeZoneRequest

logger = logging.getLogger("cctv.worker")
MAX_ATTEMPTS = max(1, int(os.getenv("CCTV_JOB_MAX_ATTEMPTS", "3")))
LEASE_SECONDS = max(10, int(os.getenv("CCTV_JOB_LEASE_SECONDS", "900")))
HEARTBEAT_INTERVAL_SECONDS = max(2, min(15, LEASE_SECONDS // 3))
_STOP_EVENT = threading.Event()


def make_worker_id(explicit: str | None = None) -> str:
    if explicit and explicit.strip():
        return explicit.strip()[:80]
    env_id = os.getenv("CCTV_WORKER_ID", "").strip()
    if env_id:
        return env_id[:80]
    host = socket.gethostname()[:24]
    return f"worker-{host}-{os.getpid()}-{uuid.uuid4().hex[:6]}"


def now_text(offset: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset)).isoformat()


def reconcile_run_status(conn, case_id: str, run_id: str | None) -> str | None:
    """Derive investigation run and case status from actual persistent job outcomes."""
    if not run_id:
        return None
    db.lock_case_row(conn, case_id)
    rows = conn.execute(
        "SELECT status FROM investigation_jobs WHERE case_id=? AND run_id=?",
        (case_id, run_id),
    ).fetchall()
    if not rows:
        return None

    statuses = [row["status"] for row in rows]
    total = len(statuses)
    completed = sum(1 for s in statuses if s == "completed")
    failed = sum(1 for s in statuses if s == "failed")
    active = sum(1 for s in statuses if s in {"queued", "running", "retrying"})

    now = now_text()
    if active > 0:
        derived = "running" if (completed > 0 or failed > 0 or any(s in {"running", "retrying"} for s in statuses)) else "queued"
        conn.execute(
            "UPDATE orchestration_runs SET status=?, updated_at=? WHERE id=? AND status NOT IN ('completed','partially_completed','failed')",
            (derived, now, run_id),
        )
        conn.execute(
            "UPDATE investigation_states SET status=?, updated_at=? WHERE case_id=?",
            (derived, now, case_id),
        )
        return derived

    if completed == total:
        terminal_status = "completed"
    elif failed == total:
        terminal_status = "failed"
    else:
        terminal_status = "partially_completed"

    prev_run = conn.execute("SELECT status FROM orchestration_runs WHERE id=?", (run_id,)).fetchone()
    prev_status = prev_run["status"] if prev_run else None
    conn.execute(
        "UPDATE orchestration_runs SET status=?, finished_at=COALESCE(finished_at, ?), updated_at=? WHERE id=?",
        (terminal_status, now, now, run_id),
    )
    conn.execute(
        "UPDATE investigation_states SET status=?, updated_at=? WHERE case_id=?",
        (terminal_status, now, case_id),
    )
    if prev_status != terminal_status:
        db.audit(
            conn,
            "investigation_run_finalized",
            {
                "run_id": run_id,
                "case_id": case_id,
                "status": terminal_status,
                "total_jobs": total,
                "completed_jobs": completed,
                "failed_jobs": failed,
            },
        )
    return terminal_status


def recover_expired() -> int:
    now = now_text()
    affected_runs: set[tuple[str, str]] = set()
    with db.connection() as conn:
        expired_rows = conn.execute(
            "SELECT id, case_id, run_id, attempts FROM investigation_jobs "
            "WHERE status='running' AND lease_expires_at IS NOT NULL AND lease_expires_at<?",
            (now,),
        ).fetchall()
        for row in expired_rows:
            if row["run_id"]:
                affected_runs.add((row["case_id"], row["run_id"]))
        exhausted = conn.execute(
            "UPDATE investigation_jobs SET status='failed', error_detail=?, finished_at=COALESCE(finished_at, ?), "
            "updated_at=?, lease_token=NULL, lease_expires_at=NULL "
            "WHERE status='running' AND lease_expires_at IS NOT NULL AND lease_expires_at<? AND attempts>=?",
            ("Worker lease expired after maximum attempts", now, now, now, MAX_ATTEMPTS),
        ).rowcount
        retry = conn.execute(
            "UPDATE investigation_jobs SET status='retrying', error_detail=?, updated_at=?, next_run_at=?, "
            "lease_token=NULL, lease_expires_at=NULL "
            "WHERE status='running' AND lease_expires_at IS NOT NULL AND lease_expires_at<? AND attempts<?",
            ("Worker lease expired; scheduled for retry", now, now, now, MAX_ATTEMPTS),
        ).rowcount
        for case_id, run_id in affected_runs:
            reconcile_run_status(conn, case_id, run_id)
    return exhausted + retry


def _claim_next_job(worker_id: str) -> tuple[str, str, str | None, str, str] | None:
    """Atomically claim one queued/retrying job and return (job_id, case_id, run_id, token, request_json)."""
    with db.connection() as conn:
        now = now_text()
        if isinstance(conn, db.PostgresConnection):
            row = conn.execute(
                "SELECT id, case_id, run_id, request_json FROM investigation_jobs "
                "WHERE status IN ('queued','retrying') AND request_json IS NOT NULL "
                "AND (next_run_at IS NULL OR next_run_at<=?) "
                "ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED",
                (now,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id, case_id, run_id, request_json FROM investigation_jobs "
                "WHERE status IN ('queued','retrying') AND request_json IS NOT NULL "
                "AND (next_run_at IS NULL OR next_run_at<=?) "
                "ORDER BY created_at LIMIT 1",
                (now,),
            ).fetchone()
        if not row:
            return None

        token = str(uuid.uuid4())
        claimed = conn.execute(
            "UPDATE investigation_jobs "
            "SET status='running', worker_id=?, attempts=attempts+1, lease_token=?, lease_expires_at=?, "
            "heartbeat_at=?, started_at=COALESCE(started_at, ?), error_detail=NULL, updated_at=? "
            "WHERE id=? AND status IN ('queued','retrying') AND (next_run_at IS NULL OR next_run_at<=?)",
            (worker_id, token, now_text(LEASE_SECONDS), now, now, now, row["id"], now),
        )
        if claimed.rowcount != 1:
            return None
        if row["run_id"]:
            reconcile_run_status(conn, row["case_id"], row["run_id"])
        return row["id"], row["case_id"], row["run_id"], token, row["request_json"]


def _heartbeat_loop(job_id: str, lease_token: str, done_event: threading.Event) -> None:
    while not done_event.wait(HEARTBEAT_INTERVAL_SECONDS):
        try:
            now = now_text()
            with db.connection() as conn:
                updated = conn.execute(
                    "UPDATE investigation_jobs SET heartbeat_at=?, lease_expires_at=?, updated_at=? "
                    "WHERE id=? AND status='running' AND lease_token=?",
                    (now, now_text(LEASE_SECONDS), now, job_id, lease_token),
                )
                if updated.rowcount != 1:
                    break
        except Exception:
            logger.debug("heartbeat failed for job %s", job_id, exc_info=True)


def run_once(worker_id: str | None = None) -> bool:
    from .main import _analyze_zone

    db.initialize()
    recover_expired()
    resolved_worker = make_worker_id(worker_id)
    claimed = _claim_next_job(resolved_worker)
    if not claimed:
        return False

    job_id, case_id, run_id, token, request_json = claimed
    done_event = threading.Event()
    hb_thread = threading.Thread(target=_heartbeat_loop, args=(job_id, token, done_event), daemon=True)
    hb_thread.start()
    try:
        payload = json.loads(request_json)
        payload["worker_id"] = resolved_worker
        request = AnalyzeZoneRequest(**payload)
        _analyze_zone(request, job_id=job_id, lease_token=token)
        with db.connection() as conn:
            reconcile_run_status(conn, case_id, run_id)
        logger.info("worker %s completed job %s", resolved_worker, job_id)
    except Exception as exc:
        with db.connection() as conn:
            job = conn.execute(
                "SELECT attempts FROM investigation_jobs WHERE id=? AND status='running' AND lease_token=?",
                (job_id, token),
            ).fetchone()
            if job:
                terminal = int(job["attempts"]) >= MAX_ATTEMPTS
                delay = min(60, 2 ** int(job["attempts"]))
                now = now_text()
                next_status = "failed" if terminal else "retrying"
                conn.execute(
                    "UPDATE investigation_jobs "
                    "SET status=?, error_detail=?, next_run_at=?, finished_at=?, updated_at=?, "
                    "lease_token=NULL, lease_expires_at=NULL "
                    "WHERE id=? AND lease_token=?",
                    (
                        next_status,
                        f"{type(exc).__name__}: {str(exc)[:300]}",
                        None if terminal else now_text(delay),
                        now if terminal else None,
                        now,
                        job_id,
                        token,
                    ),
                )
                reconcile_run_status(conn, case_id, run_id)
        logger.warning("worker %s failed job %s: %s", resolved_worker, job_id, type(exc).__name__)
    finally:
        done_event.set()
        hb_thread.join(timeout=2)
    return True


def _install_signal_handlers() -> None:
    def _handle_stop(signum, _frame):
        logger.info("Received signal %s; shutting down worker after active jobs", signum)
        _STOP_EVENT.set()

    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            try:
                signal.signal(sig, _handle_stop)
            except (ValueError, OSError):
                pass


def run_worker_loop(*, once: bool = False, concurrency: int = 1, worker_id: str | None = None) -> None:
    _STOP_EVENT.clear()
    _install_signal_handlers()
    bounded = max(1, min(4, concurrency))
    base_id = make_worker_id(worker_id)

    if once and bounded == 1:
        run_once(base_id)
        return

    def _slot_loop(slot_idx: int) -> None:
        slot_worker_id = f"{base_id}-t{slot_idx}" if bounded > 1 else base_id
        while not _STOP_EVENT.is_set():
            found = run_once(slot_worker_id)
            if once:
                break
            if not found:
                _STOP_EVENT.wait(1.5)

    with ThreadPoolExecutor(max_workers=bounded) as pool:
        futures = [pool.submit(_slot_loop, i + 1) for i in range(bounded)]
        for fut in futures:
            fut.result()


if __name__ == "__main__":
    import argparse
    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(description="Durable CCTV investigation video analysis worker")
    parser.add_argument("--once", action="store_true", help="Process available job(s) once and exit")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=int(os.getenv("VIDEO_WORKER_CONCURRENCY", "1")),
        help="Bounded concurrent worker threads per process (1-4)",
    )
    parser.add_argument("--worker-id", type=str, default=None, help="Explicit worker identity prefix")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run_worker_loop(once=args.once, concurrency=args.concurrency, worker_id=args.worker_id)
