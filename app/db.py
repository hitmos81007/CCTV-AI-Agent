from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


DATA_DIR = Path(os.getenv("APP_DATA_DIR", "./data")).resolve()
EVIDENCE_DIR = DATA_DIR / "evidence"
DB_PATH = DATA_DIR / "investigation.sqlite3"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def initialize() -> None:
    with connection() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS cases (
                case_id TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                initial_zone TEXT NOT NULL,
                neighbors_json TEXT NOT NULL DEFAULT '{}',
                clues_json TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS videos (
                video_source_id TEXT PRIMARY KEY,
                case_id TEXT NOT NULL REFERENCES cases(case_id),
                jurisdiction TEXT NOT NULL,
                camera_id TEXT NOT NULL,
                original_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                byte_size INTEGER NOT NULL,
                duration_seconds REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS videos_zone_time ON videos(case_id, jurisdiction, recorded_at);
            CREATE TABLE IF NOT EXISTS sightings (
                id TEXT PRIMARY KEY,
                case_id TEXT NOT NULL REFERENCES cases(case_id),
                worker_id TEXT NOT NULL,
                jurisdiction TEXT NOT NULL,
                camera_id TEXT NOT NULL,
                video_source_id TEXT NOT NULL REFERENCES videos(video_source_id),
                occurred_at TEXT NOT NULL,
                video_offset_seconds REAL NOT NULL,
                track_id INTEGER,
                vehicle_type TEXT NOT NULL,
                vehicle_color TEXT,
                subject_type TEXT NOT NULL DEFAULT 'vehicle',
                visual_attributes_json TEXT NOT NULL DEFAULT '[]',
                integrity_version INTEGER NOT NULL DEFAULT 1,
                detector_confidence REAL NOT NULL,
                clue_match_score REAL,
                evidence_sha256 TEXT NOT NULL,
                review_status TEXT NOT NULL DEFAULT 'pending',
                reviewer TEXT,
                review_note TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS sightings_case_time ON sightings(case_id, occurred_at);
            CREATE TABLE IF NOT EXISTS followups (
                id TEXT PRIMARY KEY,
                case_id TEXT NOT NULL REFERENCES cases(case_id),
                source_sighting_id TEXT NOT NULL REFERENCES sightings(id),
                target_zone TEXT NOT NULL,
                window_start TEXT NOT NULL,
                window_end TEXT NOT NULL,
                video_source_id TEXT REFERENCES videos(video_source_id),
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                UNIQUE(source_sighting_id, target_zone)
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                prev_hash TEXT NOT NULL,
                entry_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        columns = {row["name"] for row in db.execute("PRAGMA table_info(videos)").fetchall()}
        if "duration_seconds" not in columns:
            db.execute("ALTER TABLE videos ADD COLUMN duration_seconds REAL NOT NULL DEFAULT 0")
        sighting_columns = {row["name"] for row in db.execute("PRAGMA table_info(sightings)").fetchall()}
        if "subject_type" not in sighting_columns:
            db.execute("ALTER TABLE sightings ADD COLUMN subject_type TEXT NOT NULL DEFAULT 'vehicle'")
        if "visual_attributes_json" not in sighting_columns:
            db.execute("ALTER TABLE sightings ADD COLUMN visual_attributes_json TEXT NOT NULL DEFAULT '[]'")
        if "integrity_version" not in sighting_columns:
            db.execute("ALTER TABLE sightings ADD COLUMN integrity_version INTEGER NOT NULL DEFAULT 1")


def audit(db: sqlite3.Connection, event_type: str, payload: Any) -> str:
    last = db.execute("SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
    previous = last["entry_hash"] if last else "0" * 64
    created = utc_now()
    payload_text = canonical_json(payload)
    entry_hash = hashlib.sha256(f"{previous}|{event_type}|{created}|{payload_text}".encode("utf-8")).hexdigest()
    db.execute(
        "INSERT INTO audit_log(event_type,payload_json,prev_hash,entry_hash,created_at) VALUES(?,?,?,?,?)",
        (event_type, payload_text, previous, entry_hash, created),
    )
    return entry_hash


def verify_audit_chain() -> bool:
    with connection() as db:
        rows = db.execute("SELECT * FROM audit_log ORDER BY id").fetchall()
    previous = "0" * 64
    for row in rows:
        expected = hashlib.sha256(
            f"{previous}|{row['event_type']}|{row['created_at']}|{row['payload_json']}".encode("utf-8")
        ).hexdigest()
        if row["prev_hash"] != previous or row["entry_hash"] != expected:
            return False
        previous = row["entry_hash"]
    return True

