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
VALID_ENVS = {"development", "test", "production"}


def runtime_env() -> str:
    env = os.getenv("CCTV_ENV", "development").strip().lower() or "development"
    if env not in VALID_ENVS:
        raise RuntimeError(f"Unsupported CCTV_ENV value: {env}")
    return env


def validate_runtime_config() -> dict[str, Any]:
    env = runtime_env()
    database_url = os.getenv("DATABASE_URL", "").strip()
    if env == "production":
        if not database_url:
            raise RuntimeError("DATABASE_URL is mandatory when CCTV_ENV=production")
        if not os.getenv("SUPABASE_URL", "").strip():
            raise RuntimeError("SUPABASE_URL is mandatory when CCTV_ENV=production")
        if not (
            os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
            or os.getenv("SUPABASE_SECRET_KEY", "").strip()
        ):
            raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY or SUPABASE_SECRET_KEY is mandatory when CCTV_ENV=production")
    return {"env": env, "database_backend": "postgresql" if database_url else "sqlite"}

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
    env = runtime_env()
    database_url = os.getenv("DATABASE_URL", "").strip()
    if env == "production" and not database_url:
        raise RuntimeError("DATABASE_URL is required in production; SQLite fallback is disabled")
    if env == "test" and database_url and os.getenv("CCTV_ALLOW_TEST_REMOTE_DB", "").strip() != "1":
        raise RuntimeError("Refusing remote DATABASE_URL in test mode without CCTV_ALLOW_TEST_REMOTE_DB=1")
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
            from .migrations import apply_migrations, get_migration_status

            database_url = os.getenv("DATABASE_URL", "").strip()
            if runtime_env() == "production":
                status = get_migration_status(database_url)
                if status["pending"]:
                    names = [item["name"] for item in status["pending"]]
                    raise RuntimeError(f"Pending PostgreSQL migrations must be applied before startup: {names}")
                return
            apply_migrations(database_url)
            return
        db.executescript(schema)
        case_columns = {row["name"] for row in db.execute("PRAGMA table_info(cases)").fetchall()}
        for name, definition in (
            ("owner_id", "TEXT"),
            ("title", "TEXT"),
            ("authorized_at", "TEXT"),
            ("updated_at", "TEXT"),
        ):
            if name not in case_columns:
                db.execute("ALTER TABLE cases ADD COLUMN " + name + " " + definition)
        columns = {row["name"] for row in db.execute("PRAGMA table_info(videos)").fetchall()}
        for name, definition in (
            ("duration_seconds", "REAL NOT NULL DEFAULT 0"),
            ("upload_key", "TEXT"),
            ("source_label", "TEXT"),
            ("storage_kind", "TEXT"),
            ("storage_key", "TEXT"),
            ("media_status", "TEXT NOT NULL DEFAULT 'ready'"),
        ):
            if name not in columns:
                db.execute("ALTER TABLE videos ADD COLUMN " + name + " " + definition)
        sighting_columns = {row["name"] for row in db.execute("PRAGMA table_info(sightings)").fetchall()}
        for name, definition in (
            ("subject_type", "TEXT NOT NULL DEFAULT 'vehicle'"),
            ("visual_attributes_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("integrity_version", "INTEGER NOT NULL DEFAULT 1"),
            ("frame_path", "TEXT"),
            ("frame_sha256", "TEXT"),
            ("clue_json", "TEXT"),
            ("job_id", "TEXT"),
            ("candidate_key", "TEXT"),
            ("frame_storage_key", "TEXT"),
            ("reviewer_user_id", "TEXT"),
            ("reviewed_at", "TEXT"),
        ):
            if name not in sighting_columns:
                db.execute("ALTER TABLE sightings ADD COLUMN " + name + " " + definition)
        _migrate_registry(db)
        run_columns = {row["name"] for row in db.execute("PRAGMA table_info(orchestration_runs)").fetchall()}
        for name, definition in (
            ("start_key", "TEXT"),
            ("status", "TEXT NOT NULL DEFAULT 'planning'"),
            ("input_video_ids", "TEXT"),
            ("error_code", "TEXT"),
            ("started_at", "TEXT"),
            ("finished_at", "TEXT"),
        ):
            if name not in run_columns:
                db.execute("ALTER TABLE orchestration_runs ADD COLUMN " + name + " " + definition)
        job_columns = {row["name"] for row in db.execute("PRAGMA table_info(investigation_jobs)").fetchall()}
        for name, definition in (
            ("attempts", "INTEGER NOT NULL DEFAULT 0"),
            ("lease_token", "TEXT"),
            ("lease_expires_at", "TEXT"),
            ("next_run_at", "TEXT"),
            ("request_json", "TEXT"),
            ("run_id", "TEXT"),
            ("heartbeat_at", "TEXT"),
            ("started_at", "TEXT"),
            ("finished_at", "TEXT"),
        ):
            if name not in job_columns:
                db.execute("ALTER TABLE investigation_jobs ADD COLUMN " + name + " " + definition)
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS finding_reviews (
                id TEXT PRIMARY KEY,
                sighting_id TEXT NOT NULL REFERENCES sightings(id),
                case_id TEXT NOT NULL REFERENCES cases(case_id),
                reviewer_user_id TEXT NOT NULL,
                decision TEXT NOT NULL,
                note TEXT,
                request_key TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(sighting_id, request_key)
            );
            CREATE TABLE IF NOT EXISTS case_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT NOT NULL REFERENCES cases(case_id),
                kind TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )


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

