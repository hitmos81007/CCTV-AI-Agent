from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi.testclient import TestClient

from app import db
from app.main import _match_score, _video_runtime_status, app
from app.schemas import VehicleClue
from app.video_analysis import TARGET_CLASSES, _resolve_device


FOUR_ZONE_MAP = {
    "North": ["East", "West"],
    "West": ["North", "South"],
    "South": ["West", "East"],
    "East": ["North", "South"],
}


class VideoDeviceSelectionTests(unittest.TestCase):
    def test_auto_uses_gpu_when_available_and_cpu_otherwise(self):
        self.assertEqual(_resolve_device("auto", cuda_available=True), 0)
        self.assertEqual(_resolve_device("auto", cuda_available=False), "cpu")
        self.assertEqual(_resolve_device("cpu", cuda_available=True), "cpu")

    def test_supported_visible_targets_include_people_bicycles_and_common_vehicles(self):
        self.assertEqual(TARGET_CLASSES[0], "person")
        self.assertEqual(TARGET_CLASSES[1], "bicycle")
        self.assertEqual(TARGET_CLASSES[2], "car")
        self.assertEqual(TARGET_CLASSES[3], "motorcycle")
        self.assertNotIn("face", TARGET_CLASSES.values())


class VisualClueMatchingTests(unittest.TestCase):
    def test_person_clue_scores_only_detectable_type_and_color(self):
        clue = {
            "visual_targets": [{"target_type": "person", "color": "navy", "attributes": ["red backpack", "distinctive mark"]}],
        }
        detection = {"subject_type": "person", "vehicle_color": "navy"}
        self.assertEqual(_match_score(detection, clue), 1.0)

    def test_unverified_mark_is_not_scored_as_a_visual_match(self):
        clue = {"visual_targets": [{"target_type": "person", "color": None, "attributes": ["scar"]}]}
        detection = {"subject_type": "person", "vehicle_color": "blue"}
        self.assertEqual(_match_score(detection, clue), 0.25)  # category only; the scar is for human review

    def test_bicycle_and_unplated_vehicle_terms_are_captured_as_human_review_attributes(self):
        clue = VehicleClue(
            visual_targets=[
                {"target_type": "bicycle", "color": "red", "attributes": []},
                {"target_type": "car", "color": "white", "attributes": ["no visible plate"]},
            ],
            source_text="Synthetic description",
            parser="mock",
        )
        self.assertEqual([target.target_type for target in clue.visual_targets], ["bicycle", "car"])
        self.assertEqual(clue.visual_targets[1].attributes, ["no visible plate"])


class BackendContractTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.patchers = [
            patch.object(db, "DATA_DIR", root),
            patch.object(db, "EVIDENCE_DIR", root / "evidence"),
            patch.object(db, "DB_PATH", root / "test.sqlite3"),
            patch("app.main.REVIEW_USERNAME", "test-reviewer"),
            patch("app.main.REVIEW_PASSWORD", "test-password"),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()
        self.client.auth = ("test-reviewer", "test-password")

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
                "neighbors": FOUR_ZONE_MAP,
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
        torch_stub = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))
        with patch("app.main.importlib.util.find_spec", return_value=object()), patch.dict("sys.modules", {"torch": torch_stub}):
            response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        runtime = response.json()["video_runtime"]
        self.assertIsInstance(runtime["missing_modules"], list)
        self.assertIn("cuda_available", runtime)
        self.assertIn("gpu_inference_ready", runtime)

    def test_reviewer_routes_require_configured_basic_auth(self):
        auth = self.client.auth
        self.client.auth = None
        response = self.client.get("/api/v1/sightings")
        self.assertEqual(response.status_code, 401)
        self.client.auth = auth

    def test_review_page_serves_four_direction_upload_and_voice_controls(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-zone="North"', response.text)
        self.assertIn('data-zone="West"', response.text)
        self.assertIn('data-zone="South"', response.text)
        self.assertIn('data-zone="East"', response.text)
        self.assertIn('id="create-case"', response.text)
        self.assertIn('/api/v1/investigations/start', response.text)
        self.assertIn('id="run-search"', response.text)
        self.assertIn('/api/v1/zones/analyze', response.text)
        self.assertIn("Transcribe locally", response.text)
        self.assertIn('id="progress-fill"', response.text)
        self.assertIn("function setStage(stage)", response.text)
        self.assertIn('.zone-card[data-zone="North"]', response.text)
        self.assertIn('row.dataset.state=result.status', response.text)

    def test_voice_clue_transcription_uses_local_transcriber_and_deletes_temporary_audio(self):
        transcriber = Mock()
        transcriber.transcribe.return_value = (
            [SimpleNamespace(text="Person in navy jacket near East at 10:15 am.")],
            SimpleNamespace(language="en", language_probability=0.99),
        )
        with patch("app.main._whisper_model", return_value=transcriber):
            response = self.client.post(
                "/api/v1/clues/transcribe",
                files={"file": ("synthetic-clue.wav", b"synthetic audio fixture", "audio/wav")},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["transcript"], "Person in navy jacket near East at 10:15 am.")
        self.assertFalse(response.json()["stored"])
        audio_path = Path(transcriber.transcribe.call_args.args[0])
        self.assertFalse(audio_path.exists())

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
            visual_targets=[{"target_type": "person", "color": "navy", "attributes": ["red backpack"]}],
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
                    "neighbors": FOUR_ZONE_MAP,
                },
            )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["clue"]["vehicle_type"], "sedan")
        self.assertEqual(response.json()["clue"]["visual_targets"][0]["target_type"], "person")
        self.assertEqual(response.json()["clue"]["visual_targets"][0]["attributes"], ["red backpack"])
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
                      "neighbors": FOUR_ZONE_MAP},
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
                "jurisdiction": "Central",
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

    def test_work_item_resolver_keeps_four_zones_waiting_without_registered_videos(self):
        self.make_case()
        response = self.client.post(
            "/api/v1/work-items/resolve",
            json={
                "case_id": "SYNTH-CASE-1",
                "items": [
                    {"case_id": "SYNTH-CASE-1", "worker_id": "worker_north", "jurisdiction": zone, "video_source_id": None}
                for zone in ["North", "West", "South", "East"]
                ],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 4)
        self.assertEqual(response.json()["ready_count"], 0)
        self.assertEqual([item["status"] for item in response.json()["items"]], ["awaiting_local_video"] * 4)
        self.assertTrue(all(item["video_source_id"] is None for item in response.json()["items"]))
        self.assertEqual(self.client.get("/api/v1/sightings").json(), [])

    def test_zone_batch_dispatch_returns_four_waiting_agents_without_fabricating_sightings(self):
        self.make_case()
        response = self.client.post(
            "/api/v1/zones/analyze",
            json={
                "case_id": "SYNTH-CASE-1",
                "items": [
                    {"case_id": "SYNTH-CASE-1", "worker_id": f"zone_worker_{zone.lower()}",
                     "jurisdiction": zone, "video_source_id": None}
                    for zone in ["North", "West", "South", "East"]
                ],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["worker_count"], 4)
        self.assertEqual([item["status"] for item in response.json()["results"]], ["awaiting_local_video"] * 4)
        self.assertEqual(self.client.get("/api/v1/sightings").json(), [])

    def test_work_item_resolver_rejects_zone_outside_case_map(self):
        self.make_case()
        response = self.client.post(
            "/api/v1/work-items/resolve",
            json={"case_id": "SYNTH-CASE-1", "items": [{"jurisdiction": "Central"}]},
        )
        self.assertEqual(response.status_code, 422)

    def test_confirmed_person_candidate_queues_neighbor_zone_searches(self):
        self.make_case()
        occurred = "2026-11-22T10:00:30+00:00"
        with db.connection() as conn:
            for zone, video_id in [("North", "synthetic-north"), ("East", "synthetic-east"), ("West", "synthetic-west")]:
                conn.execute(
                    "INSERT INTO videos(video_source_id,case_id,jurisdiction,camera_id,original_name,file_path,recorded_at,sha256,byte_size,duration_seconds,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (video_id, "SYNTH-CASE-1", zone, f"CAM-{zone}", "synthetic-fixture.mp4", "fixture-not-decoded.mp4",
                     "2026-11-22T10:05:00+00:00", "b" * 64, 1, 10.0, db.utc_now()),
                )
            conn.execute(
                "INSERT INTO sightings(id,case_id,worker_id,jurisdiction,camera_id,video_source_id,occurred_at,video_offset_seconds,track_id,vehicle_type,vehicle_color,subject_type,visual_attributes_json,integrity_version,detector_confidence,clue_match_score,evidence_sha256,review_status,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("synthetic-sighting", "SYNTH-CASE-1", "worker-north", "North", "CAM-N", "synthetic-north",
                 occurred, 30.0, 7, "person", "navy", "person", '["approximate torso color: navy"]', 2,
                 0.8, 0.25, "a" * 64, "pending", db.utc_now()),
            )
        response = self.client.post(
            "/api/v1/sightings/synthetic-sighting/review",
            json={"decision": "confirmed_candidate", "reviewer": "Test Reviewer", "note": "Synthetic fixture only"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["follow_up_zones_queued"], ["East", "West"])
        ready = self.client.get("/api/v1/followups/ready?case_id=SYNTH-CASE-1")
        self.assertEqual(ready.status_code, 200)
        self.assertEqual(ready.json()["count"], 2)
        self.assertEqual({item["jurisdiction"] for item in ready.json()["items"]}, {"East", "West"})
        sightings = self.client.get("/api/v1/sightings?case_id=SYNTH-CASE-1&review_status=confirmed_candidate")
        self.assertEqual(sightings.status_code, 200)
        self.assertEqual(sightings.json()[0]["subject_type"], "person")
        self.assertEqual(sightings.json()[0]["visual_attributes"], ["approximate torso color: navy"])

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
