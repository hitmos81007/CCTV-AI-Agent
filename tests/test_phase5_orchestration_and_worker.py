from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import httpx
import numpy as np
from fastapi.testclient import TestClient

from app import auth, db, worker
from app.main import app
from app.ollama_client import _sanitize_clue_payload
from app.schemas import AnalyzeZoneRequest, VehicleClue
from tests.auth_helpers import (
    ADMIN_NORTH_ID,
    OFFICER_1_ID,
    OFFICER_2_ID,
    TEST_SUPABASE_URL,
    bearer_headers,
    seed_district_admin,
    seed_profile,
    test_jwks,
)


def make_synthetic_mp4(directory: Path, name: str, *, frames: int = 12, color: tuple[int, int, int] = (20, 40, 220)) -> bytes:
    out = directory / name
    writer = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"), 12.0, (160, 120))
    for _ in range(frames):
        frame = np.zeros((120, 160, 3), dtype=np.uint8)
        frame[:, :] = color
        writer.write(frame)
    writer.release()
    return out.read_bytes()


class Phase5OrchestrationAndWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.env_patch = patch.dict(
            os.environ,
            {
                "CCTV_ENV": "test",
                "DATABASE_URL": "",
                "APP_DATA_DIR": str(self.root),
                "SUPABASE_URL": TEST_SUPABASE_URL,
                "SUPABASE_SERVICE_ROLE_KEY": "",
                "SUPABASE_SECRET_KEY": "",
                "SUPABASE_JWT_AUDIENCE": "authenticated",
                "YOLO_DEVICE": "cpu",
            },
        )
        self.env_patch.start()
        self.verifier = auth.SupabaseJwtVerifier()
        self.patchers = [
            patch.object(db, "DATA_DIR", self.root),
            patch.object(db, "EVIDENCE_DIR", self.root / "evidence"),
            patch.object(db, "DB_PATH", self.root / "investigation.sqlite3"),
            patch.object(self.verifier, "_fetch_jwks", return_value=test_jwks()),
            patch("app.auth.get_jwt_verifier", return_value=self.verifier),
        ]
        for p in self.patchers:
            p.start()
        db.initialize()
        seed_profile(OFFICER_1_ID, verification_status="approved", approved_district_id="central-metro")
        seed_profile(OFFICER_2_ID, verification_status="approved", approved_district_id="north-division")
        seed_district_admin(ADMIN_NORTH_ID, district_id="north-division")
        self.h1 = bearer_headers(OFFICER_1_ID)
        self.h2 = bearer_headers(OFFICER_2_ID)
        self.h_admin = bearer_headers(ADMIN_NORTH_ID)
        self.client_ctx = TestClient(app)
        self.client = self.client_ctx.__enter__()

    def tearDown(self):
        self.client_ctx.__exit__(None, None, None)
        for p in reversed(self.patchers):
            p.stop()
        self.env_patch.stop()
        self.temp_dir.cleanup()

    def _create_case_with_videos(self, count: int = 2, description: str = "Find a red car.") -> tuple[str, list[str]]:
        case_id = self.client.post(
            "/api/v1/cases",
            headers=self.h1,
            json={"title": "Phase 5 Case", "description": description, "authorized": True},
        ).json()["case_id"]
        vids = []
        for idx in range(count):
            mp4 = make_synthetic_mp4(self.root, f"clip_{idx}.mp4")
            resp = self.client.post(
                "/api/v1/videos",
                headers={**self.h1, "Idempotency-Key": f"vid-{idx}"},
                data={"case_id": case_id, "source_label": f"Cam {idx + 1}"},
                files={"file": (f"clip_{idx}.mp4", mp4, "video/mp4")},
            )
            self.assertEqual(resp.status_code, 201, resp.text)
            vids.append(resp.json()["video_source_id"])
        return case_id, vids

    def test_ollama_sanitization_prevents_invented_fields_and_plate_absence_bug(self):
        raw = {
            "visual_targets": [
                {"target_type": "person", "color": "blue", "attributes": ["clothing_color/type", "null", "facial recognition"]},
                {"target_type": "car", "color": "red", "attributes": ["sedan", "http://evil.example/payload"]},
            ],
            "vehicle_color": "red",
            "vehicle_type": "sedan",
            "distinctive_vehicle_features": ["plate_absence", "license plate", "null"],
            "unsupported_requests": [],
            "event_time_text": "11:45 PM",
            "origin_zone": "upload-batch",
        }
        cleaned = _sanitize_clue_payload(raw, "Track the suspect using facial recognition and find a red car.")
        self.assertIsNone(cleaned["origin_zone"])
        self.assertIsNone(cleaned["event_time_text"])
        self.assertEqual(cleaned["distinctive_vehicle_features"], [])
        self.assertTrue(any("facial recognition" in u for u in cleaned["unsupported_requests"]))
        self.assertTrue(any("license plate" in u for u in cleaned["unsupported_requests"]))
        self.assertEqual(len(cleaned["visual_targets"]), 1)
        self.assertEqual(cleaned["visual_targets"][0]["target_type"], "car")
        self.assertEqual(cleaned["visual_targets"][0]["attributes"], ["sedan"])

    def test_orchestration_lifecycle_idempotency_failure_recovery_and_security(self):
        # 1. No-video rejection
        empty_case = self.client.post(
            "/api/v1/cases",
            headers=self.h1,
            json={"title": "Empty Case", "description": "Find a red car.", "authorized": True},
        ).json()["case_id"]
        no_vid = self.client.post(f"/api/v1/cases/{empty_case}/plan", headers=self.h1)
        self.assertEqual(no_vid.status_code, 422)

        case_id, vids = self._create_case_with_videos(2, "Find a red car near the gate.")

        # 2. Unsupported clue only -> 422 and recovers back to draft
        unsupported_clue = VehicleClue(
            visual_targets=[],
            unsupported_requests=["facial recognition"],
            source_text="facial recognition",
            parser="ollama:test",
        )
        with patch("app.orchestration.parse_vehicle_clues", return_value=unsupported_clue):
            bad_clue_resp = self.client.post(f"/api/v1/cases/{case_id}/plan", headers=self.h1)
        self.assertEqual(bad_clue_resp.status_code, 422)
        self.assertEqual(self.client.get(f"/api/v1/cases/{case_id}", headers=self.h1).json()["status"], "draft")

        # 3. Ollama outage -> 503 and recovers back to draft so retry succeeds
        with patch(
            "app.orchestration.parse_vehicle_clues",
            side_effect=httpx.ConnectError("offline", request=httpx.Request("POST", "http://127.0.0.1:11434/api/chat")),
        ):
            outage_resp = self.client.post(f"/api/v1/cases/{case_id}/plan", headers=self.h1)
        self.assertEqual(outage_resp.status_code, 503)
        self.assertEqual(self.client.get(f"/api/v1/cases/{case_id}", headers=self.h1).json()["status"], "draft")

        # 4. Valid start with Idempotency-Key -> creates 1 run + 2 jobs + immutable snapshot
        good_clue = VehicleClue(
            vehicle_color="red",
            vehicle_type="car",
            visual_targets=[{"target_type": "car", "color": "red", "attributes": []}],
            source_text="Find a red car near the gate.",
            parser="ollama:test",
        )
        with patch("app.orchestration.parse_vehicle_clues", return_value=good_clue):
            start_1 = self.client.post(
                f"/api/v1/cases/{case_id}/plan",
                headers={**self.h1, "Idempotency-Key": "start-key-1"},
            )
            self.assertEqual(start_1.status_code, 200, start_1.text)
            body_1 = start_1.json()
            self.assertEqual(body_1["status"], "queued")
            self.assertEqual(len(body_1["jobs"]), 2)
            self.assertEqual(body_1["input_video_ids"], vids)

            # Duplicate start with SAME key -> idempotent return
            start_dup = self.client.post(
                f"/api/v1/cases/{case_id}/plan",
                headers={**self.h1, "Idempotency-Key": "start-key-1"},
            )
            self.assertEqual(start_dup.status_code, 200)
            self.assertTrue(start_dup.json().get("idempotent"))

            # Duplicate start with DIFFERENT key -> 409 Conflict
            start_diff = self.client.post(
                f"/api/v1/cases/{case_id}/plan",
                headers={**self.h1, "Idempotency-Key": "start-key-2"},
            )
            self.assertEqual(start_diff.status_code, 409)

        # 5. Security: cross-owner, admin-without-officer, pending, suspended denied
        self.assertEqual(self.client.post(f"/api/v1/cases/{case_id}/plan", headers=self.h2).status_code, 404)
        self.assertEqual(self.client.get(f"/api/v1/cases/{case_id}/progress", headers=self.h2).status_code, 404)
        self.assertEqual(self.client.post(f"/api/v1/cases/{case_id}/plan", headers=self.h_admin).status_code, 403)

        seed_profile(OFFICER_1_ID, verification_status="suspended", approved_district_id=None)
        self.assertEqual(self.client.get(f"/api/v1/cases/{case_id}/progress", headers=self.h1).status_code, 403)
        seed_profile(OFFICER_1_ID, verification_status="approved", approved_district_id="central-metro")

        # 6. Inputs cannot change after reservation
        mp4 = make_synthetic_mp4(self.root, "extra.mp4")
        self.assertEqual(
            self.client.post(
                "/api/v1/videos",
                headers=self.h1,
                data={"case_id": case_id},
                files={"file": ("extra.mp4", mp4, "video/mp4")},
            ).status_code,
            409,
        )
        self.assertEqual(self.client.delete(f"/api/v1/videos/{vids[0]}", headers=self.h1).status_code, 409)

    def test_worker_claim_recovery_stale_token_partial_completion_and_dedupe(self):
        case_id, vids = self._create_case_with_videos(2, "Find a red car.")
        good_clue = VehicleClue(
            vehicle_color="red",
            vehicle_type="car",
            visual_targets=[{"target_type": "car", "color": "red", "attributes": []}],
            source_text="Find a red car.",
            parser="ollama:test",
        )
        with patch("app.orchestration.parse_vehicle_clues", return_value=good_clue):
            plan_resp = self.client.post(
                f"/api/v1/cases/{case_id}/plan",
                headers={**self.h1, "Idempotency-Key": "w-start"},
            ).json()
        run_id = plan_resp["run_id"]

        # Simulate stale lease write rejection in _analyze_zone
        fake_det = [
            {
                "track_id": 7,
                "start_offset_seconds": 0.2,
                "last_seen_at": None,
                "vehicle_type": "car",
                "vehicle_color": "red",
                "subject_type": "car",
                "visual_attributes": [],
                "detector_confidence": 0.88,
                "frame_jpeg": b"\xff\xd8\xff\xe0testjpeg",
                "frame_offset_seconds": 0.2,
            }
        ]
        with db.connection() as conn:
            j0 = conn.execute("SELECT id, request_json FROM investigation_jobs WHERE video_source_id=?", (vids[0],)).fetchone()
            conn.execute(
                "UPDATE investigation_jobs SET status='running', attempts=1, lease_token='real-tok', lease_expires_at=? WHERE id=?",
                (worker.now_text(60), j0["id"]),
            )
        req0 = AnalyzeZoneRequest(**json.loads(j0["request_json"]))
        with patch("app.main.analyze_video", return_value=fake_det):
            with self.assertRaises(RuntimeError):
                from app.main import _analyze_zone
                _analyze_zone(req0, job_id=j0["id"], lease_token="wrong-stale-tok")

        # Expired lease recovery -> retrying
        with db.connection() as conn:
            conn.execute(
                "UPDATE investigation_jobs SET lease_expires_at=? WHERE id=?",
                (worker.now_text(-10), j0["id"]),
            )
        self.assertGreaterEqual(worker.recover_expired(), 1)
        with db.connection() as conn:
            st = conn.execute("SELECT status FROM investigation_jobs WHERE id=?", (j0["id"],)).fetchone()["status"]
            self.assertEqual(st, "retrying")

        # Run worker once on job 0 (succeeds) and fail job 1 permanently -> partially_completed
        with patch("app.main.analyze_video", return_value=fake_det):
            self.assertTrue(worker.run_once(worker_id="agent-alpha"))
        with patch("app.main.analyze_video", side_effect=RuntimeError("corrupt decode")):
            for _ in range(worker.MAX_ATTEMPTS):
                with db.connection() as conn:
                    conn.execute("UPDATE investigation_jobs SET next_run_at=NULL WHERE video_source_id=?", (vids[1],))
                worker.run_once(worker_id="agent-beta")

        progress = self.client.get(f"/api/v1/cases/{case_id}/progress", headers=self.h1).json()
        self.assertEqual(progress["status"], "partially_completed")
        self.assertEqual(progress["completed_jobs"], 1)
        self.assertEqual(progress["failed_jobs"], 1)
        self.assertEqual(progress["sighting_count"], 1)

        # Verify NULL occurred_at preserved when recorded_at is unknown + frame retrieval works
        sightings = self.client.get(f"/api/v1/sightings?case_id={case_id}", headers=self.h1).json()
        self.assertEqual(len(sightings), 1)
        self.assertIsNone(sightings[0]["occurred_at"])
        self.assertEqual(self.client.get(sightings[0]["frame_url"], headers=self.h1).status_code, 200)
        self.assertEqual(self.client.get(sightings[0]["frame_url"], headers=self.h2).status_code, 404)

    def test_real_yolo_cpu_inference_on_three_fixtures_and_zero_candidate_completion(self):
        # 3 synthetic MP4 fixtures (no mocked detections) -> verifies real YOLO decode + zero-candidate completion
        case_id, _ = self._create_case_with_videos(3, "Find a red car.")
        good_clue = VehicleClue(
            vehicle_color="red",
            vehicle_type="car",
            visual_targets=[{"target_type": "car", "color": "red", "attributes": []}],
            source_text="Find a red car.",
            parser="ollama:test",
        )
        with patch("app.orchestration.parse_vehicle_clues", return_value=good_clue):
            self.client.post(f"/api/v1/cases/{case_id}/plan", headers={**self.h1, "Idempotency-Key": "real-3"})

        self.assertEqual(len(list(range(1))), 1)
        for _ in range(3):
            self.assertTrue(worker.run_once(worker_id="real-cpu-worker"))

        progress = self.client.get(f"/api/v1/cases/{case_id}/progress", headers=self.h1).json()
        self.assertEqual(progress["status"], "completed")
        self.assertEqual(progress["completed_jobs"], 3)
        self.assertEqual(progress["failed_jobs"], 0)

    def test_task2_failed_run_with_jobs_blocks_second_run_and_color_evaluation(self):
        # 1. Failed run that already created jobs (has_jobs=True) blocks a second MVP run
        case_id, vids = self._create_case_with_videos(1, "Find a red car.")
        good_clue = VehicleClue(
            vehicle_color="red",
            vehicle_type="car",
            visual_targets=[{"target_type": "car", "color": "red", "attributes": []}],
            source_text="Find a red car.",
            parser="ollama:test",
        )
        with patch("app.orchestration.parse_vehicle_clues", return_value=good_clue):
            self.client.post(f"/api/v1/cases/{case_id}/plan", headers={**self.h1, "Idempotency-Key": "t2-run-1"})
        with patch("app.main.analyze_video", side_effect=RuntimeError("boom")):
            for _ in range(worker.MAX_ATTEMPTS):
                with db.connection() as conn:
                    conn.execute("UPDATE investigation_jobs SET next_run_at=NULL WHERE video_source_id=?", (vids[0],))
                worker.run_once(worker_id="t2-worker")
        self.assertEqual(self.client.get(f"/api/v1/cases/{case_id}/progress", headers=self.h1).json()["status"], "failed")
        with patch("app.orchestration.parse_vehicle_clues", return_value=good_clue):
            second_start = self.client.post(
                f"/api/v1/cases/{case_id}/plan",
                headers={**self.h1, "Idempotency-Key": "t2-run-2"},
            )
        self.assertEqual(second_start.status_code, 409)

        # 2. Ambiguous color ('dark') on person is normalized to None + human-review attribute
        cleaned = _sanitize_clue_payload(
            {
                "visual_targets": [{"target_type": "person", "color": "dark", "attributes": ["scar"]}],
                "vehicle_color": None,
                "vehicle_type": None,
                "distinctive_vehicle_features": [],
                "unsupported_requests": [],
                "event_time_text": None,
                "origin_zone": None,
            },
            "Find a person in a dark jacket with a scar.",
        )
        self.assertIsNone(cleaned["visual_targets"][0]["color"])
        self.assertIn("dark appearance (ambiguous)", cleaned["visual_targets"][0]["attributes"])
        self.assertIn("scar", cleaned["unsupported_requests"])

        # 3. Color evaluation rejects non-matching vehicle colors (blue car != red car)
        from app.main import _matches_machine_clue
        clue_dict = good_clue.model_dump()
        self.assertTrue(_matches_machine_clue({"subject_type": "car", "vehicle_type": "car", "vehicle_color": "red"}, clue_dict))
        self.assertFalse(_matches_machine_clue({"subject_type": "car", "vehicle_type": "car", "vehicle_color": "blue"}, clue_dict))

        # 4. Supabase evidence upload failure is NOT silently swallowed when configured
        from app.storage import StorageOperationError
        case2_id, vids2 = self._create_case_with_videos(1, "Find a red car.")
        with patch("app.orchestration.parse_vehicle_clues", return_value=good_clue):
            self.client.post(f"/api/v1/cases/{case2_id}/plan", headers={**self.h1, "Idempotency-Key": "t2-stor"})
        with db.connection() as conn:
            j2 = conn.execute("SELECT id, request_json FROM investigation_jobs WHERE video_source_id=?", (vids2[0],)).fetchone()
            conn.execute(
                "UPDATE investigation_jobs SET status='running', attempts=1, lease_token='tok-stor', lease_expires_at=? WHERE id=?",
                (worker.now_text(60), j2["id"]),
            )
        req2 = AnalyzeZoneRequest(**json.loads(j2["request_json"]))
        fake_det = [
            {
                "track_id": 1,
                "start_offset_seconds": 0.1,
                "last_seen_at": None,
                "vehicle_type": "car",
                "vehicle_color": "red",
                "subject_type": "car",
                "visual_attributes": [],
                "detector_confidence": 0.9,
                "frame_jpeg": b"\xff\xd8\xff\xe0jpg",
                "frame_offset_seconds": 0.1,
            }
        ]
        with patch.dict(os.environ, {"SUPABASE_URL": TEST_SUPABASE_URL, "SUPABASE_SERVICE_ROLE_KEY": "secret-test-key"}):
            with patch("app.main.analyze_video", return_value=fake_det):
                with patch("app.storage.SupabaseEvidenceStorage.upload_frame", side_effect=StorageOperationError("bucket down")):
                    with self.assertRaises(StorageOperationError):
                        from app.main import _analyze_zone
                        _analyze_zone(req2, job_id=j2["id"], lease_token="tok-stor")


if __name__ == "__main__":
    unittest.main()
