from __future__ import annotations

import os

os.environ["CCTV_ENV"] = "test"
os.environ["DATABASE_URL"] = ""

import hashlib
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from fastapi.testclient import TestClient

from app import auth, db
from app.main import app
from tests.auth_helpers import (
    OFFICER_1_ID,
    OFFICER_2_ID,
    TEST_SUPABASE_URL,
    bearer_headers,
    seed_profile,
    test_jwks,
)


def make_test_jpeg(width: int = 160, height: int = 120, color: tuple[int, int, int] = (40, 200, 80)) -> bytes:
    img = np.zeros((height, width, 3), dtype=np.uint8)
    img[:, :] = color
    _, enc = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    return enc.tobytes()


class Phase6EvidenceAndReviewTests(unittest.TestCase):
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

        seed_profile(OFFICER_1_ID, verification_status="approved", approved_district_id="north-division")
        seed_profile(OFFICER_2_ID, verification_status="approved", approved_district_id="south-division")

        self.client = TestClient(app)
        self.officer_headers = bearer_headers(user_id=OFFICER_1_ID, email="officer1@police.gov.in")
        self.officer2_headers = bearer_headers(user_id=OFFICER_2_ID, email="officer2@police.gov.in")

    def tearDown(self):
        for p in reversed(self.patchers):
            p.stop()
        self.env_patch.stop()
        self.temp_dir.cleanup()

    def _create_case(self, title: str = "Evidence Test Case") -> str:
        resp = self.client.post(
            "/api/v1/cases",
            json={"title": title, "description": "Looking for red sedan", "authorized": True, "initial_zone": "north-division"},
            headers={**self.officer_headers, "Idempotency-Key": f"case-{uuid.uuid4().hex}"},
        )
        self.assertEqual(resp.status_code, 201)
        return resp.json()["case_id"]

    def _seed_sighting(
        self,
        case_id: str,
        *,
        frame_bytes: bytes,
        bbox: list[int] | None = None,
        frame_sha256: str | None = None,
        frame_storage_key: str | None = None,
        tamper_file: bool = False,
    ) -> str:
        sighting_id = f"sigt-{uuid.uuid4().hex[:12]}"
        actual_sha256 = hashlib.sha256(frame_bytes).hexdigest()
        stored_sha256 = frame_sha256 if frame_sha256 is not None else actual_sha256

        frame_path = db.EVIDENCE_DIR / f"{sighting_id}.jpg"
        if tamper_file:
            # write corrupted bytes
            frame_path.write_bytes(b"corrupted_tampered_bytes")
        else:
            frame_path.write_bytes(frame_bytes)

        attr_payload = {
            "tags": ["red", "car"],
            "bbox": bbox,
            "scaled_bbox": bbox,
            "frame_width": 160,
            "frame_height": 120,
            "saved_frame_width": 160,
            "saved_frame_height": 120,
        }

        with db.connection() as conn:
            # Ensure video and job exist for foreign key
            conn.execute(
                "INSERT OR IGNORE INTO videos(video_source_id, case_id, jurisdiction, camera_id, original_name, file_path, recorded_at, sha256, byte_size, duration_seconds, created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("vid-01", case_id, "north", "CAM-01", "test.mp4", "/tmp/test.mp4", db.utc_now(), "fakehash", 100, 10.0, db.utc_now()),
            )
            conn.execute(
                "INSERT INTO sightings(id, case_id, job_id, candidate_key, worker_id, jurisdiction, camera_id, "
                "video_source_id, occurred_at, video_offset_seconds, track_id, vehicle_type, vehicle_color, subject_type, "
                "visual_attributes_json, integrity_version, clue_json, frame_path, frame_storage_key, frame_sha256, "
                "detector_confidence, clue_match_score, evidence_sha256, review_status, created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    sighting_id,
                    case_id,
                    "job-1",
                    sighting_id,
                    "agent-1",
                    "north",
                    "CAM-01",
                    "vid-01",
                    db.utc_now(),
                    4.5,
                    1,
                    "car",
                    "red",
                    "car",
                    db.canonical_json(attr_payload),
                    4,
                    None,
                    str(frame_path),
                    frame_storage_key,
                    stored_sha256,
                    0.88,
                    1.0,
                    "dummy_evidence_sha256",
                    "pending",
                    db.utc_now(),
                ),
            )
        return sighting_id

    def test_evidence_frame_hash_verification_success(self):
        case_id = self._create_case()
        jpeg = make_test_jpeg(160, 120)
        sighting_id = self._seed_sighting(case_id, frame_bytes=jpeg, bbox=[20, 20, 80, 80])

        resp = self.client.get(f"/api/v1/sightings/{sighting_id}/frame", headers=self.officer_headers)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["content-type"], "image/jpeg")
        self.assertEqual(resp.headers["Cache-Control"], "private, no-store, no-cache, must-revalidate")
        self.assertEqual(resp.headers["X-Evidence-Integrity"], "verified")
        self.assertEqual(resp.headers["X-Evidence-Type"], "unannotated_captured_frame")
        self.assertEqual(hashlib.sha256(resp.content).hexdigest(), hashlib.sha256(jpeg).hexdigest())

    def test_evidence_frame_hash_verification_mismatch_rejected(self):
        case_id = self._create_case()
        jpeg = make_test_jpeg(160, 120)
        # Seeds with tampered bytes on disk compared to stored hash
        sighting_id = self._seed_sighting(case_id, frame_bytes=jpeg, bbox=[20, 20, 80, 80], tamper_file=True)

        resp = self.client.get(f"/api/v1/sightings/{sighting_id}/frame", headers=self.officer_headers)
        self.assertEqual(resp.status_code, 409)
        self.assertIn("stored frame hash mismatch", resp.json()["detail"])

    def test_annotated_frame_visualization_success(self):
        case_id = self._create_case()
        jpeg = make_test_jpeg(160, 120)
        sighting_id = self._seed_sighting(case_id, frame_bytes=jpeg, bbox=[10, 10, 100, 80])

        resp = self.client.get(f"/api/v1/sightings/{sighting_id}/frame?annotated=true", headers=self.officer_headers)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["content-type"], "image/jpeg")
        self.assertEqual(resp.headers["Cache-Control"], "private, no-store, no-cache, must-revalidate")
        self.assertEqual(resp.headers["X-Evidence-Type"], "annotated_evidence_visualization")
        self.assertEqual(resp.headers["X-Evidence-Integrity"], "verified")
        # Annotated visualization must be generated bytes, not identical to raw bytes
        self.assertNotEqual(resp.content, jpeg)

    def test_annotated_frame_missing_bbox_raises_422(self):
        case_id = self._create_case()
        jpeg = make_test_jpeg(160, 120)
        # Seed sighting with NO bbox metadata
        sighting_id = self._seed_sighting(case_id, frame_bytes=jpeg, bbox=None)

        resp = self.client.get(f"/api/v1/sightings/{sighting_id}/frame?annotated=true", headers=self.officer_headers)
        self.assertEqual(resp.status_code, 422)
        self.assertIn("Bounding-box annotation metadata is unavailable", resp.json()["detail"])

    def test_annotated_frame_out_of_bounds_bbox_raises_422(self):
        case_id = self._create_case()
        jpeg = make_test_jpeg(160, 120)
        # Coordinates exceed image dimensions (160x120)
        sighting_id = self._seed_sighting(case_id, frame_bytes=jpeg, bbox=[0, 0, 500, 500])

        resp = self.client.get(f"/api/v1/sightings/{sighting_id}/frame?annotated=true", headers=self.officer_headers)
        self.assertEqual(resp.status_code, 422)
        self.assertIn("exceed frame dimensions", resp.json()["detail"])

    def test_evidence_access_enforces_officer_ownership(self):
        case_id = self._create_case()
        jpeg = make_test_jpeg(160, 120)
        sighting_id = self._seed_sighting(case_id, frame_bytes=jpeg, bbox=[10, 10, 50, 50])

        # Another officer cannot access the frame
        resp = self.client.get(f"/api/v1/sightings/{sighting_id}/frame", headers=self.officer2_headers)
        self.assertEqual(resp.status_code, 404)

    def test_clues_parse_draft_only_restriction(self):
        case_id = self._create_case()

        # Update case status to 'running'
        with db.connection() as conn:
            conn.execute("UPDATE investigation_states SET status='running' WHERE case_id=?", (case_id,))

        resp = self.client.post(
            "/api/v1/clues/parse",
            json={"case_id": case_id, "description": "Red Honda sedan", "initial_zone": "north-division"},
            headers=self.officer_headers,
        )
        self.assertEqual(resp.status_code, 409)
        self.assertIn("cannot be modified once processing has begun", resp.json()["detail"])

    def test_list_sightings_storage_reference_and_no_leak(self):
        case_id = self._create_case()
        jpeg = make_test_jpeg(160, 120)
        sighting_id = self._seed_sighting(
            case_id,
            frame_bytes=jpeg,
            bbox=[10, 10, 50, 50],
            frame_storage_key="evidence/case_123/sigt_456.jpg",
        )

        resp = self.client.get(f"/api/v1/sightings?case_id={case_id}", headers=self.officer_headers)
        self.assertEqual(resp.status_code, 200)
        items = resp.json()
        self.assertEqual(len(items), 1)
        record = items[0]
        self.assertEqual(record["id"], sighting_id)
        self.assertEqual(record["frame_url"], f"/api/v1/sightings/{sighting_id}/frame")
        self.assertEqual(record["annotated_frame_url"], f"/api/v1/sightings/{sighting_id}/frame?annotated=true")
        self.assertEqual(record["has_frame"], True)
        self.assertEqual(record["bbox"], [10, 10, 50, 50])
        # Ensure private filesystem paths and storage keys are NOT leaked
        self.assertNotIn("frame_storage_key", record)
        self.assertNotIn("frame_path", record)

    def test_durable_human_reviews_and_idempotency(self):
        case_id = self._create_case()
        jpeg = make_test_jpeg(160, 120)
        sighting_id = self._seed_sighting(case_id, frame_bytes=jpeg, bbox=[10, 10, 50, 50])

        request_key = f"review-key-{uuid.uuid4().hex}"
        # 1. First review: Confirm candidate
        resp = self.client.post(
            f"/api/v1/sightings/{sighting_id}/review",
            json={"decision": "confirmed_candidate", "note": "Clear match of red sedan", "request_key": request_key},
            headers=self.officer_headers,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["review_status"], "confirmed_candidate")

        # Verify persisted in finding_reviews table
        with db.connection() as conn:
            rev_row = conn.execute(
                "SELECT id, sighting_id, case_id, decision, note, request_key FROM finding_reviews WHERE sighting_id=?",
                (sighting_id,),
            ).fetchone()
            self.assertIsNotNone(rev_row)
            self.assertEqual(rev_row["decision"], "confirmed_candidate")
            self.assertEqual(rev_row["note"], "Clear match of red sedan")
            self.assertEqual(rev_row["request_key"], request_key)

            # Check case events emitted
            events = conn.execute("SELECT kind FROM case_events WHERE case_id=?", (case_id,)).fetchall()
            kinds = [e["kind"] for e in events]
            self.assertIn("review_changed", kinds)
            self.assertIn("finding_changed", kinds)

        # 2. Duplicate review with identical request_key (Idempotent replay)
        resp_idem = self.client.post(
            f"/api/v1/sightings/{sighting_id}/review",
            json={"decision": "confirmed_candidate", "note": "Clear match of red sedan", "request_key": request_key},
            headers=self.officer_headers,
        )
        self.assertEqual(resp_idem.status_code, 200)
        self.assertTrue(resp_idem.json().get("idempotent"))

        # 3. Conflicting review with same request_key but different decision
        resp_conflict = self.client.post(
            f"/api/v1/sightings/{sighting_id}/review",
            json={"decision": "not_relevant", "note": "Changed mind", "request_key": request_key},
            headers=self.officer_headers,
        )
        self.assertEqual(resp_conflict.status_code, 409)
        self.assertIn("Idempotency conflict", resp_conflict.json()["detail"])

        # 4. Review history retrieval
        hist_resp = self.client.get(f"/api/v1/sightings/{sighting_id}/reviews", headers=self.officer_headers)
        self.assertEqual(hist_resp.status_code, 200)
        history = hist_resp.json()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["decision"], "confirmed_candidate")
        self.assertEqual(history[0]["note"], "Clear match of red sedan")


if __name__ == "__main__":
    unittest.main()
