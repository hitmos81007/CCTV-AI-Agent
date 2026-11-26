from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

from app import db
from app.migrations import discover_migrations
from app.storage import (
    StorageConfigError,
    StorageOperationError,
    SupabaseEvidenceStorage,
    UnsafeStorageKeyError,
    build_evidence_frame_key,
)


class Phase2MigrationAndConfigTests(unittest.TestCase):
    def test_migrations_are_ordered_sequentially(self):
        migrations = discover_migrations()
        versions = [m.version for m in migrations]
        self.assertEqual(versions, sorted(versions))
        self.assertGreaterEqual(len(versions), 4)
        self.assertEqual(versions[:4], [1, 2, 3, 4])

    def test_production_env_fails_closed_without_database_url(self):
        with patch.dict(os.environ, {"CCTV_ENV": "production", "DATABASE_URL": ""}, clear=False):
            with self.assertRaises(RuntimeError):
                db.validate_runtime_config()
            with self.assertRaises(RuntimeError):
                with db.connection():
                    pass

    def test_test_env_blocks_accidental_remote_database_url(self):
        with patch.dict(
            os.environ,
            {
                "CCTV_ENV": "test",
                "DATABASE_URL": "postgresql://postgres:pw@localhost:5432/postgres",
                "CCTV_ALLOW_TEST_REMOTE_DB": "",
            },
            clear=False,
        ):
            with self.assertRaises(RuntimeError):
                with db.connection():
                    pass

    def test_initialize_refuses_silent_remote_postgres_migrations_in_dev(self):
        fake_pg = db.PostgresConnection(Mock())
        with (
            patch.dict(os.environ, {"CCTV_ENV": "development", "DATABASE_URL": "postgresql://u:p@h:5432/db"}, clear=False),
            patch("app.db.connection") as mock_conn,
            patch("app.migrations.get_migration_status", return_value={"applied": [1, 2, 3], "pending": [{"version": 4, "name": "004_mvp_schema_rls_realtime.sql"}], "total": 4}),
        ):
            mock_conn.return_value.__enter__.return_value = fake_pg
            with self.assertRaises(RuntimeError) as ctx:
                db.initialize()
            self.assertIn("app.migrations --apply", str(ctx.exception))

    def test_apply_migrations_acquires_advisory_lock_and_rolls_back_on_error(self):
        from app.migrations import MIGRATION_LOCK_KEY, apply_migrations

        with tempfile.TemporaryDirectory() as tmp:
            mig_dir = Path(tmp)
            (mig_dir / "001_bad.sql").write_text("CREATE TABLE bad(id INT);", encoding="utf-8")
            fake_cur = Mock()
            fake_cur.fetchone.return_value = (None,)
            fake_cur.execute.side_effect = [None, None, RuntimeError("simulated SQL failure")]
            fake_conn = Mock()
            fake_conn.cursor.return_value.__enter__ = Mock(return_value=fake_cur)
            fake_conn.cursor.return_value.__exit__ = Mock(return_value=False)
            fake_conn.__enter__ = Mock(return_value=fake_conn)
            fake_conn.__exit__ = Mock(return_value=False)

            with patch("psycopg.connect", return_value=fake_conn):
                with self.assertRaises(RuntimeError):
                    apply_migrations("postgresql://u:p@h:5432/db", migration_dir=mig_dir)

            self.assertEqual(fake_cur.execute.call_args_list[0].args, ("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_KEY,)))
            fake_conn.rollback.assert_called_once()
            fake_conn.commit.assert_not_called()


class Phase2SqliteSchemaCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.patchers = [
            patch.object(db, "DATA_DIR", root),
            patch.object(db, "EVIDENCE_DIR", root / "evidence"),
            patch.object(db, "DB_PATH", root / "phase2_test.sqlite3"),
        ]
        for p in self.patchers:
            p.start()
        with patch.dict(os.environ, {"CCTV_ENV": "test", "DATABASE_URL": ""}, clear=False):
            db.initialize()

    def tearDown(self):
        for p in reversed(self.patchers):
            p.stop()
        self.temp_dir.cleanup()

    def test_populated_legacy_fixture_preserves_ids_and_hashes_across_initialize(self):
        import sqlite3

        legacy_db = db.DB_PATH
        conn = sqlite3.connect(legacy_db)
        conn.execute("DROP TABLE IF EXISTS finding_reviews")
        conn.execute("DROP TABLE IF EXISTS case_events")
        conn.commit()
        conn.close()

        now = db.utc_now()
        with patch.dict(os.environ, {"CCTV_ENV": "test", "DATABASE_URL": ""}, clear=False):
            with db.connection() as c:
                c.execute(
                    "INSERT INTO cases(case_id,description,initial_zone,neighbors_json,created_at) VALUES(?,?,?,?,?)",
                    ("LEGACY-1", "Old case", "north", "{}", now),
                )
                c.execute(
                    "INSERT INTO videos(video_source_id,case_id,jurisdiction,camera_id,original_name,file_path,recorded_at,sha256,byte_size,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    ("LVID-1", "LEGACY-1", "north", "cam1", "old.mp4", "/tmp/old.mp4", now, "f" * 64, 512, now),
                )
                c.execute(
                    "INSERT INTO sightings(id,case_id,worker_id,jurisdiction,camera_id,video_source_id,occurred_at,video_offset_seconds,vehicle_type,detector_confidence,evidence_sha256,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("LSIGHT-1", "LEGACY-1", "w1", "north", "cam1", "LVID-1", now, 3.0, "car", 0.88, "e" * 64, now),
                )
            db.initialize()
            with db.connection() as c:
                case_row = c.execute("SELECT case_id, owner_id FROM cases WHERE case_id='LEGACY-1'").fetchone()
                sight_row = c.execute("SELECT id, evidence_sha256 FROM sightings WHERE id='LSIGHT-1'").fetchone()
                self.assertEqual(case_row["case_id"], "LEGACY-1")
                self.assertIsNone(case_row["owner_id"])
                self.assertEqual(sight_row["evidence_sha256"], "e" * 64)

    def test_unknown_timestamps_and_review_idempotency(self):
        now = db.utc_now()
        with patch.dict(os.environ, {"CCTV_ENV": "test", "DATABASE_URL": ""}, clear=False):
            with db.connection() as conn:
                conn.execute(
                    "INSERT INTO cases(case_id,owner_id,title,description,initial_zone,neighbors_json,created_at) VALUES(?,?,?,?,?,?,?)",
                    ("CASE-1", str(uuid.uuid4()), "Title", "Find red car", "upload-batch", "{}", now),
                )
                conn.execute(
                    "INSERT INTO videos(video_source_id,case_id,jurisdiction,camera_id,original_name,file_path,recorded_at,sha256,byte_size,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    ("VID-1", "CASE-1", "upload-batch", "cam1", "clip.mp4", "/tmp/clip.mp4", "", "a" * 64, 100, now),
                )
                conn.execute(
                    "INSERT INTO sightings(id,case_id,worker_id,jurisdiction,camera_id,video_source_id,occurred_at,video_offset_seconds,vehicle_type,detector_confidence,evidence_sha256,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("SIGHT-1", "CASE-1", "w1", "upload-batch", "cam1", "VID-1", "", 2.0, "car", 0.9, "b" * 64, now),
                )
                conn.execute(
                    "INSERT INTO finding_reviews(id,sighting_id,case_id,reviewer_user_id,decision,note,request_key,created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), "SIGHT-1", "CASE-1", str(uuid.uuid4()), "confirmed_candidate", "Valid", "req-1", now),
                )
                with self.assertRaises(Exception):
                    conn.execute(
                        "INSERT INTO finding_reviews(id,sighting_id,case_id,reviewer_user_id,decision,note,request_key,created_at) VALUES(?,?,?,?,?,?,?,?)",
                        (str(uuid.uuid4()), "SIGHT-1", "CASE-1", str(uuid.uuid4()), "not_relevant", "Dup", "req-1", now),
                    )


class Phase2StorageAbstractionTests(unittest.TestCase):
    def test_build_evidence_frame_key_validates_inputs(self):
        owner = str(uuid.uuid4())
        key = build_evidence_frame_key(owner, "CASE-1", "VID-1", "SIGHT-1")
        self.assertEqual(key, f"{owner}/CASE-1/VID-1/SIGHT-1/frame.jpg")

        with self.assertRaises(UnsafeStorageKeyError):
            build_evidence_frame_key("not-a-uuid", "CASE-1", "VID-1", "SIGHT-1")
        with self.assertRaises(UnsafeStorageKeyError):
            build_evidence_frame_key(owner, "../escape", "VID-1", "SIGHT-1")
        with self.assertRaises(UnsafeStorageKeyError):
            build_evidence_frame_key(owner, "CASE-1", "VID-1", "SIGHT-1", "other.png")

    def test_storage_requires_server_credentials(self):
        with patch.dict(
            os.environ,
            {"SUPABASE_URL": "", "SUPABASE_SERVICE_ROLE_KEY": "", "SUPABASE_SECRET_KEY": ""},
            clear=False,
        ):
            with self.assertRaises(StorageConfigError):
                SupabaseEvidenceStorage()

    def test_storage_accepts_secret_key_fallback_and_computes_sha256(self):
        with patch.dict(
            os.environ,
            {
                "SUPABASE_URL": "https://example.supabase.co",
                "SUPABASE_SERVICE_ROLE_KEY": "",
                "SUPABASE_SECRET_KEY": "sb_secret_test",
            },
            clear=False,
        ):
            storage = SupabaseEvidenceStorage()
            payload = b"synthetic-jpeg-bytes"
            expected_sha = hashlib.sha256(payload).hexdigest()
            mock_resp = Mock(spec=httpx.Response, status_code=200)
            with patch("httpx.Client.post", return_value=mock_resp) as post_mock:
                result = storage.upload_frame(f"{uuid.uuid4()}/C1/V1/S1/frame.jpg", payload)
            self.assertEqual(result.sha256, expected_sha)
            self.assertEqual(result.byte_size, len(payload))
            self.assertEqual(post_mock.call_args.kwargs["headers"]["x-upsert"], "false")

    def test_verify_bucket_private_rejects_public_bucket(self):
        storage = SupabaseEvidenceStorage(supabase_url="https://example.supabase.co", service_key="srv")
        mock_resp = Mock(spec=httpx.Response, status_code=200)
        mock_resp.json.return_value = {"id": "cctv-evidence", "name": "cctv-evidence", "public": True}
        with patch("httpx.Client.get", return_value=mock_resp):
            with self.assertRaises(StorageOperationError):
                storage.verify_bucket_private()
