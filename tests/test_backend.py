from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from app import db
from app.main import _video_runtime_status, app
from app.schemas import VehicleClue


class BackendContractTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.patchers = [
            patch.object(db, "DATA_DIR", root),
            patch.object(db, "EVIDENCE_DIR", root / "evidence"),
            patch.object(db, "DB_PATH", root / "test.sqlite3"),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp_dir.cleanup()

    def make_case(self):
        return self.client.post(
            "/api/v1/cases",
            json={
                "case_id": "SYNTH-CASE-1",
                "description": "Synthetic test case; no real incident.",
                "initial_zone": "North",
                "neighbors": {"North": ["East", "West"]},
            },
        )

    def test_vehicle_clue_normalizes_model_literal_null_strings(self):
        clue = VehicleClue(
            vehicle_color="white",
            vehicle_type="sedan",
            event_time_text=" NULL ",
            origin_zone="null",
            source_text="Synthetic test only",
            parser="mock",
        )
        self.assertIsNone(clue.event_time_text)
        self.assertIsNone(clue.origin_zone)
        self.assertEqual(clue.vehicle_color, "white")

    def test_video_runtime_reports_missing_optional_stack_without_claiming_gpu_ready(self):
        with patch("app.main.importlib.util.find_spec", return_value=None):
            status = _video_runtime_status()
        self.assertEqual(status["missing_modules"], ["torch", "opencv", "ultralytics"])
        self.assertIsNone(status["cuda_available"])
        self.assertFalse(status["gpu_inference_ready"])

    def test_health_includes_video_runtime_readiness(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        runtime = response.json()["video_runtime"]
        self.assertIsInstance(runtime["missing_modules"], list)
        self.assertIn("cuda_available", runtime)
        self.assertIn("gpu_inference_ready", runtime)

    def test_case_can_be_saved_and_retrieved(self):
        created = self.make_case()
        self.assertEqual(created.status_code, 201)
        listed = self.client.get("/api/v1/cases")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()[0]["case_id"], "SYNTH-CASE-1")
        self.assertEqual(listed.json()[0]["neighbors"]["North"], ["East", "West"])

    def test_start_investigation_creates_case_and_saves_local_clue_result(self):
        clue = VehicleClue(
            vehicle_color="blue", vehicle_type="sedan", distinctive_vehicle_features=[],
            event_time_text="10:15 am", origin_zone="North",
            source_text="Synthetic demo only: blue sedan near North Gate at 10:15 am.",
            parser="ollama:test-model",
        )
        with patch("app.main.parse_vehicle_clues", new=AsyncMock(return_value=clue)) as parser:
            response = self.client.post(
                "/api/v1/investigations/start",
                json={
                    "case_id": "SYNTH-START-1",
                    "description": clue.source_text,
                    "initial_zone": "North",
                    "neighbors": {"North": ["East", "West"]},
                },
            )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["clue"]["vehicle_type"], "sedan")
        self.assertEqual(response.json()["neighbors"]["North"], ["East", "West"])
        self.assertIsNotNone(self.client.get("/api/v1/cases").json()[0]["clues"])
        parser.assert_awaited_once()

    def test_start_investigation_returns_registered_zone_videos_for_worker_assignment(self):
        self.make_case()
        with db.connection() as conn:
            conn.execute(
                "INSERT INTO videos(video_source_id,case_id,jurisdiction,camera_id,original_name,file_path,recorded_at,sha256,byte_size,duration_seconds,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("synthetic-video-east", "SYNTH-CASE-1", "East", "CAM-E1", "sample.mp4", "unused-test-fixture.mp4",
                 "2026-01-01T10:00:00+00:00", "a" * 64, 123, 5.0, db.utc_now()),
            )
        clue = VehicleClue(
            vehicle_color="blue", vehicle_type="sedan", distinctive_vehicle_features=[],
            event_time_text="10:15 am", origin_zone="North",
            source_text="Synthetic demo only.", parser="ollama:test-model",
        )
        with patch("app.main.parse_vehicle_clues", new=AsyncMock(return_value=clue)):
            response = self.client.post(
                "/api/v1/investigations/start",
                json={"case_id": "SYNTH-CASE-1", "description": "Synthetic test case; no real incident.", "initial_zone": "North",
                      "neighbors": {"North": ["East", "West"]}},
            )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["registered_videos"][0]["video_source_id"], "synthetic-video-east")
        self.assertEqual(response.json()["registered_videos"][0]["jurisdiction"], "East")
        self.assertEqual(self.client.get("/api/v1/sightings").json(), [])

    def test_missing_video_id_is_rejected_before_analysis(self):
        self.make_case()
        response = self.client.post(
            "/api/v1/zone/analyze",
            json={
                "case_id": "SYNTH-CASE-1",
                "worker_id": "zone_worker_1",
                "jurisdiction": "North",
                "video_source_id": None,
                "analysis_request": {"description": "Synthetic test only"},
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.get("/api/v1/sightings").json(), [])

    def test_analysis_rejects_undeclared_zone_before_looking_for_video(self):
        self.make_case()
        response = self.client.post(
            "/api/v1/zone/analyze",
            json={
                "case_id": "SYNTH-CASE-1",
                "worker_id": "worker_south",
                "jurisdiction": "South",
                "video_source_id": "never-uploaded",
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"], "Jurisdiction is not part of this case's zone map")
        self.assertEqual(self.client.get("/api/v1/sightings").json(), [])

    def test_followup_dispatch_is_empty_without_real_jobs(self):
        self.make_case()
        response = self.client.get("/api/v1/followups/ready")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"count": 0, "items": []})
        self.assertEqual(self.client.get("/api/v1/sightings").json(), [])

    def test_work_item_resolver_keeps_three_zones_waiting_without_registered_videos(self):
        self.make_case()
        response = self.client.post(
            "/api/v1/work-items/resolve",
            json={
                "case_id": "SYNTH-CASE-1",
                "items": [
                    {"case_id": "SYNTH-CASE-1", "worker_id": "worker_north", "jurisdiction": zone, "video_source_id": None}
                    for zone in ["North", "East", "West"]
                ],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 3)
        self.assertEqual(response.json()["ready_count"], 0)
        self.assertEqual([item["status"] for item in response.json()["items"]], ["awaiting_local_video"] * 3)
        self.assertTrue(all(item["video_source_id"] is None for item in response.json()["items"]))
        self.assertEqual(self.client.get("/api/v1/sightings").json(), [])

    def test_work_item_resolver_rejects_zone_outside_case_map(self):
        self.make_case()
        response = self.client.post(
            "/api/v1/work-items/resolve",
            json={"case_id": "SYNTH-CASE-1", "items": [{"jurisdiction": "South"}]},
        )
        self.assertEqual(response.status_code, 422)

    def test_unregistered_video_cannot_create_sightings(self):
        self.make_case()
        response = self.client.post(
            "/api/v1/zone/analyze",
            json={
                "case_id": "SYNTH-CASE-1",
                "worker_id": "zone_worker_1",
                "jurisdiction": "North",
                "video_source_id": "not-registered",
                "description": "Synthetic test only",
            },
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.get("/api/v1/sightings").json(), [])

    def test_audit_chain_verifies_case_creation(self):
        self.make_case()
        self.assertTrue(self.client.get("/api/v1/integrity").json()["audit_chain_valid"])

    def test_audit_chain_detects_payload_tampering(self):
        self.make_case()
        with db.connection() as conn:
            conn.execute("UPDATE audit_log SET payload_json='{}' WHERE event_type='case_created'")
        result = self.client.get("/api/v1/integrity").json()
        self.assertFalse(result["audit_chain_valid"])
        self.assertFalse(result["valid"])

    def test_missing_local_ollama_is_reported_without_fabricating_clues(self):
        self.make_case()
        with patch("app.main.parse_vehicle_clues", side_effect=httpx.ConnectError("offline", request=httpx.Request("POST", "http://localhost:11434/api/chat"))):
            response = self.client.post(
                "/api/v1/clues/parse",
                json={"case_id": "SYNTH-CASE-1", "description": "Synthetic test only", "initial_zone": "North"},
            )
        self.assertEqual(response.status_code, 503)
        self.assertIsNone(self.client.get("/api/v1/cases").json()[0]["clues"])

    def test_invalid_local_ollama_output_is_reported_without_saving_clues(self):
        self.make_case()
        with patch("app.main.parse_vehicle_clues", side_effect=ValueError("invalid clue JSON")):
            response = self.client.post(
                "/api/v1/clues/parse",
                json={"case_id": "SYNTH-CASE-1", "description": "Synthetic test only", "initial_zone": "North"},
            )
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["detail"], "Ollama response did not match the clue schema")
        self.assertIsNone(self.client.get("/api/v1/cases").json()[0]["clues"])


if __name__ == "__main__":
    unittest.main()
