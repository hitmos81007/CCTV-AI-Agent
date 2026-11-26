import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import db
from app.main import app


class PhaseTwoContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patches = [
            patch.object(db, "DATA_DIR", root),
            patch.object(db, "EVIDENCE_DIR", root / "evidence"),
            patch.object(db, "DB_PATH", root / "case.sqlite3"),
        ]
        for item in self.patches:
            item.start()
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()
        self.zones = {"North": ["East"], "East": ["North"], "West": [], "South": [], "Central": [], "Harbor": []}
        response = self.client.post("/api/v1/cases", json={
            "case_id": "FICTIONAL-1",
            "description": "Fictional stolen blue vehicle",
            "initial_zone": "North",
            "neighbors": self.zones,
        })
        self.assertEqual(response.status_code, 201, response.text)

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_six_zones_camera_and_recording_registry(self):
        self.assertEqual(self.client.get("/api/v1/cases/FICTIONAL-1/jurisdictions").json()["neighbors"], self.zones)
        self.assertEqual(len(self.client.get("/api/v1/agents?case_id=FICTIONAL-1").json()), 6)
        camera = {"camera_id": "CAM-1", "name": "Example camera", "jurisdiction": "Harbor",
                  "latitude": 13.0, "longitude": 80.0, "source_type": "ip_camera"}
        self.assertEqual(self.client.post("/api/v1/cameras", json=camera).status_code, 201)
        request = {"case_id": "FICTIONAL-1", "camera_id": "CAM-1", "source_type": "ip_camera",
                   "source_uri": "rtsp://192.0.2.1/live"}
        self.assertEqual(self.client.post("/api/v1/recordings", json=request).status_code, 201)
        registry = self.client.get("/api/v1/recordings?case_id=FICTIONAL-1").json()
        self.assertEqual(registry["sources"][0]["status"], "registered")
        request["source_uri"] = "rtsp://user:secret@192.0.2.1/live"
        self.assertEqual(self.client.post("/api/v1/recordings", json=request).status_code, 422)

    def test_rejects_unknown_jurisdiction_relationship(self):
        case = {"case_id": "BAD", "description": "Fictional case", "initial_zone": "North",
                "neighbors": {"North": ["Missing"]}}
        self.assertEqual(self.client.post("/api/v1/cases", json=case).status_code, 422)

    def test_processing_job_completes_and_failure_is_persisted(self):
        with db.connection() as conn:
            conn.execute(
                "INSERT INTO videos(video_source_id,case_id,jurisdiction,camera_id,original_name,file_path,recorded_at,sha256,byte_size,duration_seconds,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("clip", "FICTIONAL-1", "North", "CAM-1", "clip.mp4", "unused.mp4",
                 "2026-01-01T00:00:00+00:00", "a" * 64, 0, 1, db.utc_now()),
            )
        payload = {"case_id": "FICTIONAL-1", "jurisdiction": "North", "worker_id": "north", "video_source_id": "clip"}
        with patch("app.main._analyze_zone", return_value={"candidate_sightings": []}):
            self.assertEqual(self.client.post("/api/v1/zone/analyze", json=payload).status_code, 200)
        with patch("app.main._analyze_zone", side_effect=ValueError("fixture failure")):
            with self.assertRaises(ValueError):
                self.client.post("/api/v1/zone/analyze", json=payload)
        jobs = self.client.get("/api/v1/cases/FICTIONAL-1/progress").json()["jobs"]
        self.assertEqual({job["status"] for job in jobs}, {"completed", "failed"})
