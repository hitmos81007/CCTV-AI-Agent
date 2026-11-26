"""Ordered, transactional PostgreSQL migration runner."""
from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

MIGRATION_DIR = Path(__file__).resolve().parent.parent / "migrations" / "postgresql"
MIGRATION_FILENAME_RE = re.compile(r"^(\d{3})_([A-Za-z0-9_]+)\.sql$")
MIGRATION_LOCK_KEY = 71920101


@dataclass(frozen=True)
class MigrationFile:
    version: int
    name: str
    path: Path


def discover_migrations(migration_dir: Path = MIGRATION_DIR) -> list[MigrationFile]:
    if not migration_dir.exists():
        raise FileNotFoundError(f"Migration directory not found: {migration_dir}")
    discovered: list[MigrationFile] = []
    seen_versions: set[int] = set()
    for entry in sorted(migration_dir.iterdir(), key=lambda p: p.name):
        if not entry.is_file() or entry.suffix.lower() != ".sql":
            continue
        match = MIGRATION_FILENAME_RE.match(entry.name)
        if not match:
            raise ValueError(f"Invalid migration filename format: {entry.name}")
        version = int(match.group(1))
        if version in seen_versions:
            raise ValueError(f"Duplicate migration version {version} in {migration_dir}")
        seen_versions.add(version)
        discovered.append(MigrationFile(version=version, name=entry.name, path=entry))
    discovered.sort(key=lambda item: item.version)
    return discovered


def _get_applied_versions(cur) -> set[int]:
    cur.execute("SELECT to_regclass('public.schema_migrations')")
    row = cur.fetchone()
    exists = row[0] if isinstance(row, tuple) else row["to_regclass"]
    if not exists:
        return set()
    cur.execute("SELECT version FROM public.schema_migrations ORDER BY version")
    rows = cur.fetchall()
    return {int(r[0] if isinstance(r, tuple) else r["version"]) for r in rows}


def get_migration_status(database_url: str, migration_dir: Path = MIGRATION_DIR) -> dict[str, object]:
    import psycopg

    migrations = discover_migrations(migration_dir)
    with psycopg.connect(database_url, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            applied = _get_applied_versions(cur)
    pending = [m for m in migrations if m.version not in applied]
    return {
        "applied": sorted(applied),
        "pending": [{"version": m.version, "name": m.name} for m in pending],
        "total": len(migrations),
    }


def apply_migrations(
    database_url: str,
    migration_dir: Path = MIGRATION_DIR,
    *,
    dry_run: bool = False,
    target_versions: Iterable[int] | None = None,
) -> list[int]:
    import psycopg

    migrations = discover_migrations(migration_dir)
    if target_versions is not None:
        allowed = set(target_versions)
        migrations = [m for m in migrations if m.version in allowed]

    newly_applied: list[int] = []
    with psycopg.connect(database_url, connect_timeout=15) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_KEY,))
                applied = _get_applied_versions(cur)
                for migration in migrations:
                    if migration.version in applied:
                        continue
                    sql_text = migration.path.read_text(encoding="utf-8")
                    if not sql_text.strip():
                        raise ValueError(f"Migration {migration.name} is empty")
                    cur.execute(sql_text)
                    cur.execute(
                        "INSERT INTO public.schema_migrations(version, applied_at) VALUES(%s, %s) ON CONFLICT(version) DO NOTHING",
                        (migration.version, datetime.now(timezone.utc).isoformat()),
                    )
                    newly_applied.append(migration.version)
            if dry_run:
                conn.rollback()
            else:
                conn.commit()
        except Exception:
            conn.rollback()
            raise
    return newly_applied


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser(description="PostgreSQL migration runner for CCTV-AI-Agent")
    parser.add_argument("--status", action="store_true", help="Print applied and pending migrations")
    parser.add_argument("--apply", action="store_true", help="Apply pending migrations")
    parser.add_argument("--dry-run", action="store_true", help="Run pending migrations and roll back")
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("DATABASE_URL is not configured.")

    if args.apply and args.dry_run:
        raise SystemExit("Choose either --apply or --dry-run, not both.")

    if args.status or (not args.apply and not args.dry_run):
        status = get_migration_status(database_url)
        print(f"Applied versions: {status['applied']}")
        print(f"Pending migrations: {status['pending']}")
        return 0

    applied = apply_migrations(database_url, dry_run=args.dry_run)
    mode = "DRY RUN (rolled back)" if args.dry_run else "APPLIED"
    print(f"{mode} migration versions: {applied}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
