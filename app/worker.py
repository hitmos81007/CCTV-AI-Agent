"""Lease-based worker for persisted LangGraph video jobs."""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()

from . import db
from .schemas import AnalyzeZoneRequest

logger = logging.getLogger("cctv.worker")
MAX_ATTEMPTS = 3
LEASE_SECONDS = max(5, int(os.getenv("CCTV_JOB_LEASE_SECONDS", "900")))


def now_text(offset: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset)).isoformat()


def recover_expired() -> int:
    now = now_text()
    with db.connection() as conn:
        exhausted = conn.execute(
            "UPDATE investigation_jobs SET status='failed',error_detail=?,updated_at=?,lease_token=NULL,lease_expires_at=NULL WHERE status='running' AND lease_expires_at<? AND attempts>=?",
            ("Worker lease expired after maximum attempts", now, now, MAX_ATTEMPTS),
        ).rowcount
        retry = conn.execute(
            "UPDATE investigation_jobs SET status='retrying',error_detail=?,updated_at=?,next_run_at=?,lease_token=NULL,lease_expires_at=NULL WHERE status='running' AND lease_expires_at<? AND attempts<?",
            ("Worker lease expired; scheduled for retry", now, now, now, MAX_ATTEMPTS),
        ).rowcount
    return exhausted + retry


def run_once() -> bool:
    from .main import _analyze_zone

    db.initialize()
    recover_expired()
    with db.connection() as conn:
        now = now_text()
        row = conn.execute(
            "SELECT id,request_json FROM investigation_jobs WHERE status IN ('queued','retrying') AND request_json IS NOT NULL AND (next_run_at IS NULL OR next_run_at<=?) ORDER BY created_at LIMIT 1",
            (now,),
        ).fetchone()
        if not row:
            return False
        token = str(uuid.uuid4())
        claimed = conn.execute(
            "UPDATE investigation_jobs SET status='running',attempts=attempts+1,lease_token=?,lease_expires_at=?,updated_at=? WHERE id=? AND status IN ('queued','retrying') AND (next_run_at IS NULL OR next_run_at<=?)",
            (token, now_text(LEASE_SECONDS), now, row["id"], now),
        )
        if claimed.rowcount != 1:
            return True
    try:
        request = AnalyzeZoneRequest(**json.loads(row["request_json"]))
        _analyze_zone(request, job_id=row["id"], lease_token=token)
        logger.info("job %s completed", row["id"])
    except Exception as exc:
        with db.connection() as conn:
            job = conn.execute("SELECT attempts FROM investigation_jobs WHERE id=? AND status='running' AND lease_token=?",
                               (row["id"], token)).fetchone()
            if job:
                terminal = job["attempts"] >= MAX_ATTEMPTS
                delay = min(60, 2 ** job["attempts"])
                conn.execute("UPDATE investigation_jobs SET status=?,error_detail=?,next_run_at=?,updated_at=?,lease_token=NULL,lease_expires_at=NULL WHERE id=? AND lease_token=?",
                             ("failed" if terminal else "retrying", type(exc).__name__, now_text(delay), now_text(), row["id"], token))
        logger.warning("job %s failed: %s", row["id"], type(exc).__name__)
    return True


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    while True:
        found = run_once()
        if args.once:
            break
        if not found:
            time.sleep(2)
