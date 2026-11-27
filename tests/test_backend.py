from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from app import auth, db
from app.main import _analyze_zone, _match_score, _video_runtime_status, app
from app.schemas import AnalyzeZoneRequest, VehicleClue
from app.video_analysis import TARGET_CLASSES, _resolve_device
from tests.auth_helpers import OFFICER_1_ID, TEST_SUPABASE_URL, bearer_headers, seed_profile, test_jwks

FOUR_ZONE_MAP = {
    "North": ["East", "West"],
    "West": ["North", "South"],
    "South": ["West", "East"],
    "East": ["North", "South"],
}


class N8nWorkflowExportTests(unittest.TestCase):
    def test_zone_batch_switch_routes_all_zone_items_to_backend_guard(self):
        project_root = Path(__file__).resolve().parents[1]
        for name in ("CCTV-Investigation-Orchestrator.json", "CCTV-Investigation-Orchestrator-Import.json"):
            with self.subTest(workflow=name):
                workflow = json.loads((project_root / "n8n" / name).read_text(encoding="utf-8-sig"))
                nodes = {node["name"]: node for node in workflow["nodes"]}
                self.assertEqual(
                    workflow["connections"]["Aggregate Zone Work Items"]["main"][0]["node"], "Switch"
                )
                self.assertEqual(workflow["connections"]["Switch"]["main"][0][0]["node"], "Local Video API (backend required)")
                condition = nodes["Switch"]["parameters"]["rules"]["values"][0]["conditions"]["conditions"][0]
                self.assertEqual(condition["leftValue"], "={{ $json.items && $json.items.length > 0 }}")

    def test_scheduled_followup_nodes_are_connected_with_valid_dynamic_url_expression(self):
        project_root = Path(__file__).resolve().parents[1]
        for name in ("CCTV-Investigation-Orchestrator.json", "CCTV-Investigation-Orchestrator-Import.json"):
            with self.subTest(workflow=name):
                workflow = json.loads((project_root / "n8n" / name).read_text(encoding="utf-8-sig"))
                nodes = {node["name"]: node for node in workflow["nodes"]}
                connections = workflow["connections"]
                self.assertEqual(connections["Schedule Trigger"]["main"][0][0]["node"], "List Ready Follow-ups")
                self.assertEqual(connections["List Ready Follow-ups"]["main"][0][0]["node"], "Split Out")
                self.assertEqual(connections["Split Out"]["main"][0][0]["node"], "Analyze Follow-up Zone")
                self.assertEqual(
                    nodes["Analyze Follow-up Zone"]["parameters"]["url"],
                    "={{ 'http://host.docker.internal:8000' + $json.analysis_url }}",
                )


class WindowsLauncherTests(unittest.TestCase):
    def test_gpu_launcher_loads_reviewer_and_ollama_environment_file(self):
        project_root = Path(__file__).resolve().parents[1]
        launcher = (project_root / "run_backend_gpu.ps1").read_text(encoding="utf-8")
        self.assertIn("Copy-Item -LiteralPath '.env.example' -Destination '.env'", launcher)
        self.assertIn("--env-file .env", launcher)
        self.assertIn("$audioPackageTarget;$packageTarget;$projectRoot", launcher)
        self.assertIn("[string]$BindAddress = '127.0.0.1'", launcher)
        self.assertIn("--host $BindAddress", launcher)

    def test_video_launchers_default_to_loopback_and_allow_explicit_docker_binding(self):
        project_root = Path(__file__).resolve().parents[1]
        for name in ("run_backend_gpu.ps1", "run_backend_video.ps1"):
            with self.subTest(launcher=name):
                launcher = (project_root / name).read_text(encoding="utf-8")
                self.assertIn("[ValidateSet('127.0.0.1', '0.0.0.0')]", launcher)
                self.assertIn("[string]$BindAddress = '127.0.0.1'", launcher)
                self.assertIn("--host $BindAddress", launcher)

    def test_optional_audio_setup_uses_audio_requirements_and_pyav_compatibility_pin(self):
        project_root = Path(__file__).resolve().parents[1]
        setup = (project_root / "setup_audio.ps1").read_text(encoding="utf-8")
        audio_requirements = (project_root / "requirements-audio.txt").read_text(encoding="utf-8")
        self.assertIn("requirements-audio.txt", setup)
        self.assertIn("av<19", audio_requirements)


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
        self.assertEqual(_match_score(detection, clue), 0.25)

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
        self.verifier = auth.SupabaseJwtVerifier()
        self.patchers = [
            patch.object(db, "DATA_DIR", root),
            patch.object(db, "EVIDENCE_DIR", root / "evidence"),
            patch.object(db, "DB_PATH", root / "test.sqlite3"),
            patch.dict(os.environ, {"SUPABASE_URL": TEST_SUPABASE_URL, "SUPABASE_JWT_AUDIENCE": "authenticated"}),
            patch.object(self.verifier, "_fetch_jwks", return_value=test_jwks()),
            patch("app.auth.get_jwt_verifier", return_value=self.verifier),
        ]
        for patcher in self.patchers:
            patcher.start()
        seed_profile(OFFICER_1_ID, verification_status="approved", approved_district_id="north-division")
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()
        self.headers = bearer_headers(OFFICER_1_ID)

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp_dir.cleanup()

    def make_case(self):
        return self.client.post(
            "/api/v1/cases",
            headers=self.headers,
            json={
                "case_id": "SYNTH-CASE-1",
                "description": "Synthetic test case; no real incident.",
                "authorized": True,
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

    def test_health_is_minimal_without_exposing_internal_runtime(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_mvp_routes_require_bearer_auth(self):
        self.assertEqual(self.client.get("/api/v1/cases").status_code, 401)
        self.assertEqual(self.client.get("/api/v1/sightings").status_code, 401)

    def test_legacy_prototype_routes_are_disabled_with_410(self):
        self.assertEqual(self.client.get("/").status_code, 410)
        self.assertEqual(self.client.get("/api-config.js").status_code, 410)
        for method, path, payload in [
            ("GET", "/api/v1/cameras", None),
            ("GET", "/api/v1/followups", None),
            ("GET", "/api/v1/integrity", None),
            ("GET", "/api/v1/recordings?case_id=SYNTH-CASE-1", None),
            ("GET", "/api/v1/agents?case_id=SYNTH-CASE-1", None),
            ("GET", "/api/v1/followups/ready", None),
        ]:
            with self.subTest(method=method, path=path):
                resp = self.client.request(method, path, headers=self.headers, json=payload)
                self.assertEqual(resp.status_code, 410)

    def test_case_can_be_saved_and_retrieved(self):
        created = self.make_case()
        self.assertEqual(created.status_code, 201)
        listed = self.client.get("/api/v1/cases", headers=self.headers)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()[0]["case_id"], "SYNTH-CASE-1")
        self.assertEqual(listed.json()[0]["status"], "draft")

    def test_missing_video_id_is_rejected_before_analysis(self):
        self.make_case()
        response = self.client.post(
            "/api/v1/zone/analyze",
            headers=self.headers,
            json={
                "case_id": "SYNTH-CASE-1",
                "worker_id": "zone_worker_1",
                "jurisdiction": "North",
                "video_source_id": None,
                "analysis_request": {"description": "Synthetic test only"},
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.get("/api/v1/sightings", headers=self.headers).json(), [])

    def test_analysis_rejects_undeclared_zone_before_looking_for_video(self):
        self.make_case()
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            _analyze_zone(
                AnalyzeZoneRequest(
                    case_id="SYNTH-CASE-1",
                    worker_id="worker_south",
                    jurisdiction="Central",
                    video_source_id="never-uploaded",
                )
            )
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(ctx.exception.detail, "Jurisdiction is not part of this case's zone map")
        self.assertEqual(self.client.get("/api/v1/sightings", headers=self.headers).json(), [])

    def test_confirmed_person_candidate_records_review_and_audit(self):
        self.make_case()
        occurred = "2026-11-22T10:00:30+00:00"
        with db.connection() as conn:
            conn.execute(
                "INSERT INTO videos(video_source_id,case_id,jurisdiction,camera_id,original_name,file_path,recorded_at,sha256,byte_size,duration_seconds,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("synthetic-north", "SYNTH-CASE-1", "North", "CAM-North", "synthetic-fixture.mp4",
                 "fixture-not-decoded.mp4", "2026-11-22T10:05:00+00:00", "b" * 64, 1, 10.0, db.utc_now()),
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
            headers=self.headers,
            json={"decision": "confirmed_candidate", "note": "Synthetic fixture only"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["review_status"], "confirmed_candidate")
        sightings = self.client.get(
            "/api/v1/sightings?case_id=SYNTH-CASE-1&review_status=confirmed_candidate",
            headers=self.headers,
        )
        self.assertEqual(sightings.status_code, 200)
        self.assertEqual(sightings.json()[0]["subject_type"], "person")
        self.assertEqual(sightings.json()[0]["visual_attributes"], ["approximate torso color: navy"])

    def test_unregistered_video_cannot_create_sightings(self):
        self.make_case()
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            _analyze_zone(
                AnalyzeZoneRequest(
                    case_id="SYNTH-CASE-1",
                    worker_id="zone_worker_1",
                    jurisdiction="North",
                    video_source_id="not-registered",
                )
            )
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(self.client.get("/api/v1/sightings", headers=self.headers).json(), [])

    def test_audit_chain_verifies_case_creation(self):
        self.make_case()
        self.assertTrue(db.verify_audit_chain())

    def test_audit_chain_detects_payload_tampering(self):
        self.make_case()
        with db.connection() as conn:
            conn.execute("UPDATE audit_log SET payload_json='{}' WHERE event_type='case_created'")
        self.assertFalse(db.verify_audit_chain())

    def test_evidence_and_review_history_endpoints_work_for_owned_case(self):
        self.make_case()
        ev = self.client.get("/api/v1/evidence?case_id=SYNTH-CASE-1", headers=self.headers)
        self.assertEqual(ev.status_code, 200)
        self.assertEqual(ev.json(), [])
        with db.connection() as conn:
            conn.execute(
                "INSERT INTO videos(video_source_id,case_id,jurisdiction,camera_id,original_name,file_path,recorded_at,sha256,byte_size,duration_seconds,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("v-hist", "SYNTH-CASE-1", "North", "CAM-N", "hist.mp4", "unused.mp4",
                 "2026-11-22T10:00:00+00:00", "c" * 64, 1, 5.0, db.utc_now()),
            )
            conn.execute(
                "INSERT INTO sightings(id,case_id,worker_id,jurisdiction,camera_id,video_source_id,occurred_at,video_offset_seconds,track_id,vehicle_type,vehicle_color,subject_type,visual_attributes_json,integrity_version,detector_confidence,clue_match_score,evidence_sha256,review_status,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("s-hist", "SYNTH-CASE-1", "worker-north", "North", "CAM-N", "v-hist",
                 "2026-11-22T10:00:30+00:00", 30.0, 2, "car", "blue", "car", "[]", 2,
                 0.9, 1.0, "d" * 64, "pending", db.utc_now()),
            )
        rev = self.client.post(
            "/api/v1/sightings/s-hist/review",
            headers=self.headers,
            json={"decision": "not_relevant", "note": "Different vehicle model"},
        )
        self.assertEqual(rev.status_code, 200)
        hist = self.client.get("/api/v1/sightings/s-hist/reviews", headers=self.headers)
        self.assertEqual(hist.status_code, 200)
        self.assertEqual(len(hist.json()), 1)
        self.assertEqual(hist.json()[0]["decision"], "not_relevant")

    def test_missing_local_ollama_is_reported_without_fabricating_clues(self):
        self.make_case()
        with patch(
            "app.main.parse_vehicle_clues",
            side_effect=httpx.ConnectError("offline", request=httpx.Request("POST", "http://localhost:11434/api/chat")),
        ):
            response = self.client.post(
                "/api/v1/clues/parse",
                headers=self.headers,
                json={"case_id": "SYNTH-CASE-1", "description": "Synthetic test only", "initial_zone": "North"},
            )
        self.assertEqual(response.status_code, 503)
        self.assertIsNone(self.client.get("/api/v1/cases", headers=self.headers).json()[0]["clues"])

    def test_invalid_local_ollama_output_is_reported_without_saving_clues(self):
        self.make_case()
        with patch("app.main.parse_vehicle_clues", side_effect=ValueError("invalid clue JSON")):
            response = self.client.post(
                "/api/v1/clues/parse",
                headers=self.headers,
                json={"case_id": "SYNTH-CASE-1", "description": "Synthetic test only", "initial_zone": "North"},
            )
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["detail"], "Ollama response did not match the clue schema")
        self.assertIsNone(self.client.get("/api/v1/cases", headers=self.headers).json()[0]["clues"])


if __name__ == "__main__":
    unittest.main()
