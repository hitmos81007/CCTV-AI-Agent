"""Copy a verified SQLite backup to PostgreSQL, retaining original audit entries.

Usage: python scripts/migrate_sqlite_to_postgres.py --sqlite <backup> [--apply]
DATABASE_URL must be set in the environment. Without --apply all writes roll back.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
from contextlib import closing
from pathlib import Path

import psycopg
from psycopg import sql

TABLE_KEYS = {
    "cases": "case_id",
    "videos": "video_source_id",
    "sightings": "id",
    "followups": "id",
    "cameras": "camera_id",
    "recordings": "id",
    "investigation_jobs": "id",
    "investigation_states": "case_id",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    source = Path(args.sqlite).resolve(strict=True)
    database_url = os.environ["DATABASE_URL"]
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as local:
        if local.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("SQLite integrity check failed")
        if local.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("SQLite foreign-key check failed")
        tables = {r[0] for r in local.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        audit_rows = local.execute(
            "SELECT id,event_type,payload_json,prev_hash,entry_hash,created_at FROM audit_log ORDER BY id"
        ).fetchall()
        previous = "0" * 64
        for _, event, payload, prev_hash, entry_hash, created in audit_rows:
            calculated = hashlib.sha256(f"{previous}|{event}|{created}|{payload}".encode()).hexdigest()
            if prev_hash != previous or calculated != entry_hash:
                raise RuntimeError("SQLite audit chain is invalid")
            previous = entry_hash
        with psycopg.connect(database_url) as pg:
            with pg.cursor(binary=True) as cur:
                cur.execute("SELECT pg_advisory_xact_lock(71920102)")
                cur.execute("""CREATE TABLE IF NOT EXISTS imported_sqlite_audit (
                    entry_hash TEXT PRIMARY KEY, original_id BIGINT NOT NULL, event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL, prev_hash TEXT NOT NULL, created_at TEXT NOT NULL
                )""")
                cur.execute("ALTER TABLE imported_sqlite_audit ENABLE ROW LEVEL SECURITY")
                for table, key in TABLE_KEYS.items():
                    if table not in tables:
                        continue
                    local_columns = [r[1] for r in local.execute(f'PRAGMA table_info("{table}")')]
                    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s", (table,))
                    target_columns = {row[0] for row in cur.fetchall()}
                    columns = [name for name in local_columns if name in target_columns]
                    if key not in columns or not columns:
                        raise RuntimeError(f"Target schema missing expected columns for {table}")
                    projection = ",".join('"' + c + '"' for c in columns)
                    rows = local.execute(f'SELECT {projection} FROM "{table}"').fetchall()
                    query = sql.SQL("INSERT INTO {} ({}) VALUES ({}) ON CONFLICT DO NOTHING").format(
                        sql.Identifier(table), sql.SQL(",").join(map(sql.Identifier, columns)),
                        sql.SQL(",").join(sql.Placeholder() for _ in columns))
                    if rows:
                        cur.executemany(query, rows)
                        keys = [row[columns.index(key)] for row in rows]
                        cur.execute(sql.SQL("SELECT {} FROM {} WHERE {} = ANY(%s)").format(
                            sql.SQL(",").join(map(sql.Identifier, columns)), sql.Identifier(table), sql.Identifier(key)), (keys,))
                        target = {row[columns.index(key)]: row for row in cur.fetchall()}
                        mismatched = [row for row in rows if target.get(row[columns.index(key)]) != row]
                        if len(target) != len(rows) or mismatched:
                            fields = sorted({columns[i] for row in mismatched if row[columns.index(key)] in target
                                             for i, value in enumerate(row) if target[row[columns.index(key)]][i] != value})
                            raise RuntimeError(f"Target records conflict with source in {table}, fields {fields}; transaction rolled back")
                    cur.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))
                    print(f"{table}: source {len(rows)}, matching {len(rows)}, destination total {cur.fetchone()[0]}")
                cur.executemany(
                    "INSERT INTO imported_sqlite_audit(entry_hash,original_id,event_type,payload_json,prev_hash,created_at) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(entry_hash) DO NOTHING",
                    [(h, i, ev, payload, prev, created) for i, ev, payload, prev, h, created in audit_rows],
                )
                hashes = [row[4] for row in audit_rows]
                cur.execute("SELECT entry_hash,original_id,event_type,payload_json,prev_hash,created_at FROM imported_sqlite_audit WHERE entry_hash=ANY(%s)", (hashes,))
                imported = {row[0]: row for row in cur.fetchall()}
                if any(imported.get(h) != (h, i, ev, payload, prev, created) for i, ev, payload, prev, h, created in audit_rows):
                    raise RuntimeError("Archived audit entries conflict with source")
                print(f"original audit entries: source {len(audit_rows)}, matching {len(imported)}; source chain verified")
                if args.apply:
                    pg.commit()
                    print("Migration committed")
                else:
                    pg.rollback()
                    print("Dry run rolled back")


if __name__ == "__main__":
    main()
