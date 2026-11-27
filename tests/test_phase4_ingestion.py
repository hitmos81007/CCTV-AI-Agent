from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from fastapi.testclient import TestClient

from app import auth, db
from app.main import app
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


def make_synthetic_mp4(directory: Path, name: str = "clip.mp4", frames: int = 15, fps: float = 15.0) -> bytes:
    out = directory / name
    writer = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"), fps, (160, 120))
    assert writer.isOpened(), "OpenCV VideoWriter failed to open"
    for i in range(frames):
        frame = np.zeros((120, 160, 3), dtype=np.uint8)
        frame[:, :] = (20, 60 + (i * 10) % 150, 200)
        writer.write(frame)
    writer.release()
    return out.read_bytes()


class PhaseFourIngestionAndAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.verifier = auth.SupabaseJwtVerifier()
        self.patches = [
            patch.object(db, "DATA_DIR", self.root),
            patch.object(db, "EVIDENCE_DIR", self.root / "evidence"),
            patch.object(db, "DB_PATH", self.root / "phase4.sqlite3"),
            patch.dict(os.environ, {"SUPABASE_URL": TEST_SUPABASE_URL, "SUPABASE_JWT_AUDIENCE": "authenticated"}),
            patch.object(self.verifier, "_fetch_jwks", return_value=test_jwks()),
            patch("app.auth.get_jwt_verifier", return_value=self.verifier),
        ]
        for p in self.patches:
            p.start()
        db.initialize()
        seed_profile(OFFICER_1_ID, verification_status="approved", approved_district_id="north-division")
        seed_profile(OFFICER_2_ID, verification_status="approved", approved_district_id="north-division")
        seed_district_admin(ADMIN_NORTH_ID, district_id="north-division")
        self.client_ctx = TestClient(app)
        self.client = self.client_ctx.__enter__()
        self.h1 = bearer_headers(OFFICER_1_ID, email="officer1@police.example")
        self.h2 = bearer_headers(OFFICER_2_ID, email="officer2@police.example")
        self.h_admin = bearer_headers(ADMIN_NORTH_ID, email="admin@police.example")

    def tearDown(self):
        self.client_ctx.__exit__(None, None, None)
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def test_simplified_case_creation_idempotency_and_draft_update(self):
        # Unconfirmed authorization rejected
        denied = self.client.post(
            "/api/v1/cases",
            headers=self.h1,
            json={"title": "Test Case", "description": "Find a red car.", "authorized": False},
        )
        self.assertEqual(denied.status_code, 422)

        # Server-generated case_id + Idempotency-Key deduplication
        payload = {
            "title": "Central Corridor Observation",
            "description": "Find a red car in the uploaded recordings.",
            "clue_notes": "Red sedan",
            "incident_time_text": "10:15 AM",
            "authorized": True,
        }
        c1 = self.client.post("/api/v1/cases", headers={**self.h1, "Idempotency-Key": "idem-case-1"}, json=payload)
        self.assertEqual(c1.status_code, 201, c1.text)
        case_id = c1.json()["case_id"]
        self.assertTrue(case_id.startswith("CASE-"))
        self.assertEqual(c1.json()["status"], "draft")

        c2 = self.client.post("/api/v1/cases", headers={**self.h1, "Idempotency-Key": "idem-case-1"}, json=payload)
        self.assertEqual(c2.status_code, 201)
        self.assertEqual(c2.json()["case_id"], case_id)
        self.assertTrue(c2.json()["idempotent"])

        # Draft update
        upd = self.client.patch(
            f"/api/v1/cases/{case_id}",
            headers=self.h1,
            json={"title": "Updated Corridor Title", "clue_notes": "Red sedan, roof rack"},
        )
        self.assertEqual(upd.status_code, 200)
        self.assertEqual(upd.json()["title"], "Updated Corridor Title")

    def test_multi_video_upload_null_timestamp_range_playback_and_draft_delete(self):
        created = self.client.post(
            "/api/v1/cases",
            headers=self.h1,
            json={"title": "Batch Clip Case", "description": "Locate a blue motorcycle.", "authorized": True},
        )
        case_id = created.json()["case_id"]
        mp4_a = make_synthetic_mp4(self.root, "cam_a.mp4", frames=15, fps=15.0)
        mp4_b = make_synthetic_mp4(self.root, "cam_b.mp4", frames=30, fps=15.0)

        # Upload clip A with NULL recorded_at and custom upload_key
        up_a = self.client.post(
            "/api/v1/videos",
            headers={**self.h1, "Idempotency-Key": "clip-key-a"},
            data={"case_id": case_id, "source_label": "North Gate"},
            files={"file": ("cam_a.mp4", mp4_a, "video/mp4")},
        )
        self.assertEqual(up_a.status_code, 201, up_a.text)
        body_a = up_a.json()
        self.assertIsNone(body_a["recorded_at"])
        self.assertEqual(body_a["sha256"], hashlib.sha256(mp4_a).hexdigest())
        self.assertEqual(body_a["byte_size"], len(mp4_a))
        self.assertGreater(body_a["duration_seconds"], 0.5)
        self.assertEqual(body_a["width"], 160)
        self.assertEqual(body_a["height"], 120)
        self.assertNotIn("file_path", body_a)

        # Retry with same upload_key returns existing record idempotently
        up_a_retry = self.client.post(
            "/api/v1/videos",
            headers={**self.h1, "Idempotency-Key": "clip-key-a"},
            data={"case_id": case_id, "source_label": "North Gate"},
            files={"file": ("cam_a.mp4", mp4_a, "video/mp4")},
        )
        self.assertEqual(up_a_retry.status_code, 201)
        self.assertTrue(up_a_retry.json()["idempotent"])
        self.assertEqual(up_a_retry.json()["video_source_id"], body_a["video_source_id"])

        # Reusing same Idempotency-Key with DIFFERENT video content is rejected with 409
        up_a_conflict = self.client.post(
            "/api/v1/videos",
            headers={**self.h1, "Idempotency-Key": "clip-key-a"},
            data={"case_id": case_id, "source_label": "North Gate"},
            files={"file": ("cam_b.mp4", mp4_b, "video/mp4")},
        )
        self.assertEqual(up_a_conflict.status_code, 409)

        # Upload clip B with explicit ISO timestamp
        up_b = self.client.post(
            "/api/v1/videos",
            headers={**self.h1, "Idempotency-Key": "clip-key-b"},
            data={"case_id": case_id, "source_label": "South Gate", "recorded_at": "2026-11-27T09:00:00Z"},
            files={"file": ("cam_b.mp4", mp4_b, "video/mp4")},
        )
        self.assertEqual(up_b.status_code, 201, up_b.text)
        self.assertIsNotNone(up_b.json()["recorded_at"])

        # Case detail includes both videos without file_path
        detail = self.client.get(f"/api/v1/cases/{case_id}", headers=self.h1)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(len(detail.json()["videos"]), 2)
        for v in detail.json()["videos"]:
            self.assertNotIn("file_path", v)

        # HTTP Range playback (206 Partial Content)
        vid_id = body_a["video_source_id"]
        full = self.client.get(f"/api/v1/videos/{vid_id}/file", headers=self.h1)
        self.assertEqual(full.status_code, 200)
        self.assertEqual(full.content, mp4_a)

        partial = self.client.get(
            f"/api/v1/videos/{vid_id}/file",
            headers={**self.h1, "Range": "bytes=0-99"},
        )
        self.assertEqual(partial.status_code, 206)
        self.assertEqual(partial.headers.get("content-range"), f"bytes 0-99/{len(mp4_a)}")
        self.assertEqual(partial.content, mp4_a[:100])

        # Draft video removal deletes metadata & file
        deleted = self.client.delete(f"/api/v1/videos/{vid_id}", headers=self.h1)
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.client.get(f"/api/v1/videos/{vid_id}/file", headers=self.h1).status_code, 404)
        self.assertEqual(len(self.client.get(f"/api/v1/cases/{case_id}", headers=self.h1).json()["videos"]), 1)

        # Non-draft case cannot delete remaining video
        with db.connection() as conn:
            conn.execute("UPDATE investigation_states SET status='running' WHERE case_id=?", (case_id,))
        vid_b = up_b.json()["video_source_id"]
        self.assertEqual(self.client.delete(f"/api/v1/videos/{vid_b}", headers=self.h1).status_code, 409)

    def test_invalid_corrupted_oversized_and_batch_limit_rejections(self):
        case_id = self.client.post(
            "/api/v1/cases",
            headers=self.h1,
            json={"title": "Validation Case", "description": "White delivery truck.", "authorized": True},
        ).json()["case_id"]

        # Unsupported extension
        bad_ext = self.client.post(
            "/api/v1/videos",
            headers=self.h1,
            data={"case_id": case_id},
            files={"file": ("notes.txt", b"not a video", "text/plain")},
        )
        self.assertEqual(bad_ext.status_code, 415)

        # Masqueraded HTML / corrupted bytes with .mp4 extension
        fake_mp4 = self.client.post(
            "/api/v1/videos",
            headers=self.h1,
            data={"case_id": case_id},
            files={"file": ("fake.mp4", b"<html><script>alert(1)</script></html>", "video/mp4")},
        )
        self.assertEqual(fake_mp4.status_code, 422)

        # Oversized file (1 MB cap)
        with patch.dict(os.environ, {"MAX_UPLOAD_MB": "1"}):
            big = self.client.post(
                "/api/v1/videos",
                headers=self.h1,
                data={"case_id": case_id},
                files={"file": ("big.mp4", b"\x00\x00\x00\x18ftypisom" + b"\x00" * (1024 * 1024 + 64), "video/mp4")},
            )
            self.assertEqual(big.status_code, 413)

        # Batch limit (MAX_CASE_VIDEOS=1)
        mp4 = make_synthetic_mp4(self.root, "one.mp4")
        with patch.dict(os.environ, {"MAX_CASE_VIDEOS": "1"}):
            self.assertEqual(
                self.client.post(
                    "/api/v1/videos",
                    headers=self.h1,
                    data={"case_id": case_id},
                    files={"file": ("one.mp4", mp4, "video/mp4")},
                ).status_code,
                201,
            )
            second = self.client.post(
                "/api/v1/videos",
                headers=self.h1,
                data={"case_id": case_id},
                files={"file": ("two.mp4", mp4, "video/mp4")},
            )
            self.assertEqual(second.status_code, 409)

        # No leftover orphaned files for failed uploads
        stored_files = list((self.root / "evidence" / "cases" / case_id).glob("*"))
        self.assertEqual(len(stored_files), 1)

    def test_video_authorization_and_privacy_matrix(self):
        case_id = self.client.post(
            "/api/v1/cases",
            headers=self.h1,
            json={"title": "Owned Case", "description": "Red sedan.", "authorized": True},
        ).json()["case_id"]
        mp4 = make_synthetic_mp4(self.root, "owned.mp4")
        up = self.client.post(
            "/api/v1/videos",
            headers=self.h1,
            data={"case_id": case_id},
            files={"file": ("owned.mp4", mp4, "video/mp4")},
        )
        self.assertEqual(up.status_code, 201)
        vid_id = up.json()["video_source_id"]

        # 1. Anonymous cannot upload or retrieve
        self.assertEqual(
            self.client.post("/api/v1/videos", data={"case_id": case_id}, files={"file": ("a.mp4", mp4, "video/mp4")}).status_code,
            401,
        )
        self.assertEqual(self.client.get(f"/api/v1/videos/{vid_id}/file").status_code, 401)

        # 2-4. Pending, rejected, suspended officers cannot upload or retrieve
        for bad_status in ("pending_review", "rejected", "suspended"):
            seed_profile(OFFICER_2_ID, verification_status=bad_status, approved_district_id=None)
            self.assertEqual(
                self.client.post(
                    "/api/v1/videos",
                    headers=self.h2,
                    data={"case_id": case_id},
                    files={"file": ("a.mp4", mp4, "video/mp4")},
                ).status_code,
                403,
            )
            self.assertEqual(self.client.get(f"/api/v1/videos/{vid_id}/file", headers=self.h2).status_code, 403)

        # 6-7. Approved Officer 2 cannot upload to, retrieve, or delete Officer 1's video
        seed_profile(OFFICER_2_ID, verification_status="approved", approved_district_id="north-division")
        self.assertEqual(
            self.client.post(
                "/api/v1/videos",
                headers=self.h2,
                data={"case_id": case_id},
                files={"file": ("a.mp4", mp4, "video/mp4")},
            ).status_code,
            404,
        )
        self.assertEqual(self.client.get(f"/api/v1/videos/{vid_id}/file", headers=self.h2).status_code, 404)
        self.assertEqual(self.client.delete(f"/api/v1/videos/{vid_id}", headers=self.h2).status_code, 404)

        # 8. District admin (without approved officer status) cannot access officer recordings
        self.assertEqual(self.client.get(f"/api/v1/videos/{vid_id}/file", headers=self.h_admin).status_code, 403)

    def test_concurrent_uploads_cannot_exceed_max_case_videos(self):
        from concurrent.futures import ThreadPoolExecutor

        case_id = self.client.post(
            "/api/v1/cases",
            headers=self.h1,
            json={"title": "Concurrent Batch Case", "description": "Red sedan.", "authorized": True},
        ).json()["case_id"]
        mp4 = make_synthetic_mp4(self.root, "conc.mp4")

        def do_upload(idx: int) -> int:
            resp = self.client.post(
                "/api/v1/videos",
                headers={**self.h1, "Idempotency-Key": f"conc-{idx}"},
                data={"case_id": case_id},
                files={"file": (f"conc_{idx}.mp4", mp4, "video/mp4")},
            )
            return resp.status_code

        with patch.dict(os.environ, {"MAX_CASE_VIDEOS": "2"}):
            with ThreadPoolExecutor(max_workers=4) as pool:
                statuses = list(pool.map(do_upload, range(4)))
        self.assertEqual(statuses.count(201), 2)
        self.assertEqual(statuses.count(409), 2)
        stored_files = list((self.root / "evidence" / "cases" / case_id).glob("*"))
        self.assertEqual(len(stored_files), 2)

    def test_task50_security_corrections_enforced(self):
        from app.main import _reconcile_incomplete_staged_uploads

        # 1. Footage authorization confirmation is explicitly required (no default True)
        no_auth = self.client.post(
            "/api/v1/cases",
            headers=self.h1,
            json={"title": "Missing Auth", "description": "Red sedan."},
        )
        self.assertEqual(no_auth.status_code, 422)

        # 2. Client-supplied case_id rejected in non-test runtime; path traversal always rejected
        with patch.dict(os.environ, {"CCTV_ENV": "development"}):
            client_id_resp = self.client.post(
                "/api/v1/cases",
                headers=self.h1,
                json={"case_id": "CLIENT-CASE-1", "title": "T", "description": "Red sedan.", "authorized": True},
            )
            self.assertEqual(client_id_resp.status_code, 422)

        bad_id_resp = self.client.post(
            "/api/v1/cases",
            headers=self.h1,
            json={"case_id": "../escape", "title": "T", "description": "Red sedan.", "authorized": True},
        )
        self.assertEqual(bad_id_resp.status_code, 422)

        # Create valid server-generated draft case
        case_id = self.client.post(
            "/api/v1/cases",
            headers=self.h1,
            json={"title": "Security Draft", "description": "Red sedan.", "authorized": True},
        ).json()["case_id"]
        self.assertTrue(case_id.startswith("CASE-"))
        mp4 = make_synthetic_mp4(self.root, "sec.mp4")

        # 3. Path traversal case_id on upload rejected before creating directory
        trav_up = self.client.post(
            "/api/v1/videos",
            headers=self.h1,
            data={"case_id": "../escape"},
            files={"file": ("sec.mp4", mp4, "video/mp4")},
        )
        self.assertEqual(trav_up.status_code, 422)
        self.assertFalse((self.root / "evidence" / "escape").exists())

        # 4. DB transaction failure during upload cleans up staging and destination files
        with patch("app.main.db.audit", side_effect=RuntimeError("simulated db failure")):
            with self.assertRaises(RuntimeError):
                self.client.post(
                    "/api/v1/videos",
                    headers=self.h1,
                    data={"case_id": case_id},
                    files={"file": ("sec.mp4", mp4, "video/mp4")},
                )
        case_dir = self.root / "evidence" / "cases" / case_id
        self.assertEqual(list(case_dir.glob("*")), [])
        self.assertEqual(list(case_dir.glob(".*")), [])

        # Upload one valid video while draft
        up_ok = self.client.post(
            "/api/v1/videos",
            headers=self.h1,
            data={"case_id": case_id},
            files={"file": ("sec.mp4", mp4, "video/mp4")},
        )
        self.assertEqual(up_ok.status_code, 201)
        vid_id = up_ok.json()["video_source_id"]

        # 5. Staged upload reconciliation cleans orphaned .staging-* files
        orphan = case_dir / ".staging-orphan.mp4"
        orphan.write_bytes(b"partial")
        self.assertGreaterEqual(_reconcile_incomplete_staged_uploads(max_age_seconds=0), 1)
        self.assertFalse(orphan.exists())

        # 6. Non-draft status blocks upload, case edit, and video deletion, and rejects late-completing uploads
        with db.connection() as conn:
            conn.execute("UPDATE investigation_states SET status='queued' WHERE case_id=?", (case_id,))
        self.assertEqual(
            self.client.patch(f"/api/v1/cases/{case_id}", headers=self.h1, json={"title": "Edited"}).status_code,
            409,
        )
        self.assertEqual(self.client.delete(f"/api/v1/videos/{vid_id}", headers=self.h1).status_code, 409)
        late_up = self.client.post(
            "/api/v1/videos",
            headers=self.h1,
            data={"case_id": case_id},
            files={"file": ("late.mp4", mp4, "video/mp4")},
        )
        self.assertEqual(late_up.status_code, 409)
        self.assertEqual(len(list(case_dir.glob("*"))), 1)


if __name__ == "__main__":
    unittest.main()
