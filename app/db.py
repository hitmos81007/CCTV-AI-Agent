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

class PostgresConnection:
    """Adapt the existing parameterized queries to psycopg."""

    def __init__(self, raw):
        self.raw = raw

    def execute(self, sql, params=()):
        sql = sql.replace("?", "%s")
        if "INSERT OR IGNORE INTO" in sql:
            sql = sql.replace("INSERT OR IGNORE INTO", "INSERT INTO") + " ON CONFLICT DO NOTHING"
        # Binary results preserve the exact IEEE-754 values used in evidence hashes.
        return self.raw.cursor(binary=True).execute(sql, params)

    def executescript(self, script):
        for statement in script.split(";"):
            if statement.strip():
                self.raw.execute(statement)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        if not database_url.startswith(("postgresql://", "postgres://")):
            raise RuntimeError("DATABASE_URL must be a PostgreSQL connection URL")
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("Install psycopg to use PostgreSQL") from exc
        with psycopg.connect(database_url, row_factory=dict_row) as raw:
            yield PostgresConnection(raw)
        return
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
        schema = (
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
                clue_json TEXT,
                frame_path TEXT,
                frame_sha256 TEXT,
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
        if isinstance(db, PostgresConnection):
            migration_dir = Path(__file__).resolve().parent.parent / "migrations" / "postgresql"
            exists = db.execute("SELECT to_regclass('schema_migrations') AS name").fetchone()["name"]
            applied = {row["version"] for row in db.execute("SELECT version FROM schema_migrations").fetchall()} if exists else set()
            if not {1, 2, 3}.issubset(applied):
                db.execute("SELECT pg_advisory_xact_lock(71920101)")
                exists = db.execute("SELECT to_regclass('schema_migrations') AS name").fetchone()["name"]
                applied = {row["version"] for row in db.execute("SELECT version FROM schema_migrations").fetchall()} if exists else set()
                for version, name in ((1, "001_initial.sql"), (2, "002_job_leases.sql"), (3, "003_orchestration_registry.sql")):
                    if version not in applied:
                        db.executescript((migration_dir / name).read_text(encoding="utf-8"))
            return
        db.executescript(schema)
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
        if "frame_path" not in sighting_columns:
            db.execute("ALTER TABLE sightings ADD COLUMN frame_path TEXT")
        if "frame_sha256" not in sighting_columns:
            db.execute("ALTER TABLE sightings ADD COLUMN frame_sha256 TEXT")
        if "clue_json" not in sighting_columns:
            db.execute("ALTER TABLE sightings ADD COLUMN clue_json TEXT")
        _migrate_registry(db)
        job_columns = {row["name"] for row in db.execute("PRAGMA table_info(investigation_jobs)").fetchall()}
        for name, definition in (
            ("attempts", "INTEGER NOT NULL DEFAULT 0"),
            ("lease_token", "TEXT"),
            ("lease_expires_at", "TEXT"),
            ("next_run_at", "TEXT"),
            ("request_json", "TEXT"),
        ):
            if name not in job_columns:
                db.execute("ALTER TABLE investigation_jobs ADD COLUMN " + name + " " + definition)


def _migrate_registry(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS cameras (
            camera_id TEXT PRIMARY KEY, jurisdiction TEXT NOT NULL, name TEXT NOT NULL,
            latitude REAL, longitude REAL, source_type TEXT NOT NULL, source_uri TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS recordings (
            id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(case_id),
            camera_id TEXT NOT NULL, jurisdiction TEXT NOT NULL, source_type TEXT NOT NULL,
            video_source_id TEXT REFERENCES videos(video_source_id),
            source_uri TEXT, recorded_at TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS investigation_jobs (
            id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(case_id),
            jurisdiction TEXT NOT NULL, video_source_id TEXT REFERENCES videos(video_source_id),
            worker_id TEXT NOT NULL, status TEXT NOT NULL, error_detail TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS investigation_states (
            case_id TEXT PRIMARY KEY REFERENCES cases(case_id),
            status TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS orchestration_runs (
            id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(case_id),
            state_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS orchestration_tasks (
            run_id TEXT NOT NULL REFERENCES orchestration_runs(id),
            video_source_id TEXT NOT NULL REFERENCES videos(video_source_id),
            job_id TEXT NOT NULL REFERENCES investigation_jobs(id),
            PRIMARY KEY(run_id,video_source_id)
        );
        CREATE TABLE IF NOT EXISTS video_metadata (
            video_source_id TEXT PRIMARY KEY REFERENCES videos(video_source_id),
            metadata_json TEXT NOT NULL
        );
    """)
    conn.execute("INSERT INTO schema_migrations(version,applied_at) VALUES(?,?) ON CONFLICT(version) DO NOTHING",
                 (1, utc_now()))
    conn.execute("""INSERT INTO recordings(id,case_id,camera_id,jurisdiction,source_type,video_source_id,recorded_at,status,created_at)
                    SELECT video_source_id,case_id,camera_id,jurisdiction,'upload',video_source_id,recorded_at,'ready',created_at FROM videos v
                    WHERE NOT EXISTS (SELECT 1 FROM recordings r WHERE r.video_source_id=v.video_source_id)
                    ON CONFLICT(id) DO NOTHING""")


def audit(db: sqlite3.Connection, event_type: str, payload: Any) -> str:
    if isinstance(db, PostgresConnection):
        db.execute("SELECT pg_advisory_xact_lock(71920102)")
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

