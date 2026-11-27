import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import auth, db
from app.main import app
from tests.auth_helpers import OFFICER_1_ID, TEST_SUPABASE_URL, bearer_headers, seed_profile, test_jwks


class PhaseTwoContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.verifier = auth.SupabaseJwtVerifier()
        self.patches = [
            patch.object(db, "DATA_DIR", root),
            patch.object(db, "EVIDENCE_DIR", root / "evidence"),
            patch.object(db, "DB_PATH", root / "case.sqlite3"),
            patch.dict(
                os.environ,
                {
                    "DATABASE_URL": "",
                    "CCTV_ENV": "test",
                    "SUPABASE_URL": TEST_SUPABASE_URL,
                    "SUPABASE_JWT_AUDIENCE": "authenticated",
                    "SUPABASE_SERVICE_ROLE_KEY": "",
                    "SUPABASE_SECRET_KEY": "",
                },
            ),
            patch.object(self.verifier, "_fetch_jwks", return_value=test_jwks()),
            patch("app.auth.get_jwt_verifier", return_value=self.verifier),
        ]
        for item in self.patches:
            item.start()
        seed_profile(OFFICER_1_ID, verification_status="approved", approved_district_id="north-division")
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()
        self.headers = bearer_headers(OFFICER_1_ID)
        self.zones = {"North": ["East"], "East": ["North"], "West": [], "South": [], "Central": [], "Harbor": []}
        response = self.client.post(
            "/api/v1/cases",
            headers=self.headers,
            json={
                "case_id": "FICTIONAL-1",
                "description": "Fictional stolen blue vehicle",
                "authorized": True,
                "initial_zone": "North",
                "neighbors": self.zones,
            },
        )
        self.assertEqual(response.status_code, 201, response.text)

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_mvp_case_detail_active_and_legacy_registry_routes_disabled(self):
        detail = self.client.get("/api/v1/cases/FICTIONAL-1", headers=self.headers)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["case_id"], "FICTIONAL-1")
        for method, path in [
            ("GET", "/api/v1/cases/FICTIONAL-1/jurisdictions"),
            ("GET", "/api/v1/agents?case_id=FICTIONAL-1"),
            ("GET", "/api/v1/cameras"),
            ("GET", "/api/v1/recordings?case_id=FICTIONAL-1"),
        ]:
            self.assertEqual(self.client.request(method, path, headers=self.headers).status_code, 410)

    def test_rejects_unknown_jurisdiction_relationship(self):
        case = {
            "case_id": "BAD",
            "description": "Fictional case",
            "initial_zone": "North",
            "neighbors": {"North": ["Missing"]},
        }
        self.assertEqual(self.client.post("/api/v1/cases", headers=self.headers, json=case).status_code, 422)

    def test_processing_job_completes_and_failure_is_persisted(self):
        now = db.utc_now()
        with db.connection() as conn:
            conn.execute(
                "INSERT INTO videos(video_source_id,case_id,jurisdiction,camera_id,original_name,file_path,recorded_at,sha256,byte_size,duration_seconds,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("clip", "FICTIONAL-1", "North", "CAM-1", "clip.mp4", "unused.mp4",
                 "2026-01-01T00:00:00+00:00", "a" * 64, 0, 1, now),
            )
            conn.execute(
                "INSERT INTO investigation_jobs(id,case_id,jurisdiction,video_source_id,worker_id,status,attempts,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                ("job-1", "FICTIONAL-1", "North", "clip", "north", "completed", 1, now, now),
            )
            conn.execute(
                "INSERT INTO investigation_jobs(id,case_id,jurisdiction,video_source_id,worker_id,status,attempts,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                ("job-2", "FICTIONAL-1", "South", "clip", "south", "failed", 1, now, now),
            )
        jobs = self.client.get("/api/v1/cases/FICTIONAL-1/progress", headers=self.headers).json()["jobs"]
        self.assertEqual({job["status"] for job in jobs}, {"completed", "failed"})
