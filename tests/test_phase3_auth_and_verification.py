from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import auth, db
from app.main import app
from tests.auth_helpers import (
    ADMIN_NORTH_ID,
    ADMIN_SOUTH_ID,
    OFFICER_1_ID,
    OFFICER_2_ID,
    TEST_SUPABASE_URL,
    bearer_headers,
    mint_supabase_jwt,
    seed_district_admin,
    seed_profile,
    test_jwks,
)

PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class FakeVerificationStorage:
    def __init__(self, store: dict[str, bytes]) -> None:
        self.store = store

    def upload_document(self, storage_key: str, data: bytes, content_type: str) -> str:
        self.store[storage_key] = bytes(data)
        return storage_key

    def download_document(self, storage_key: str) -> bytes:
        if storage_key not in self.store:
            raise FileNotFoundError(storage_key)
        return self.store[storage_key]

    def delete_object(self, storage_key: str) -> None:
        self.store.pop(storage_key, None)


class PhaseThreeAuthAndVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.verifier = auth.SupabaseJwtVerifier()
        self.storage_objects: dict[str, bytes] = {}
        self.fake_storage = FakeVerificationStorage(self.storage_objects)
        self.patches = [
            patch.object(db, "DATA_DIR", root),
            patch.object(db, "EVIDENCE_DIR", root / "evidence"),
            patch.object(db, "DB_PATH", root / "phase3.sqlite3"),
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
            patch("app.verification_routes.SupabaseVerificationStorage", return_value=self.fake_storage),
        ]
        for p in self.patches:
            p.start()
        db.initialize()
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def test_jwt_validation_rejects_invalid_tokens_and_supports_key_rotation(self):
        seed_profile(OFFICER_1_ID, verification_status="approved", approved_district_id="north-division")

        # Missing / malformed
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)
        self.assertEqual(
            self.client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not-a-jwt"}).status_code,
            401,
        )

        # Unsupported alg (HS256)
        hs_tok = mint_supabase_jwt(OFFICER_1_ID, alg="HS256")
        self.assertEqual(
            self.client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {hs_tok}"}).status_code,
            401,
        )

        # Expired, wrong issuer, wrong audience, invalid role, non-UUID sub
        for bad_kwargs in [
            {"exp_delta_seconds": -120},
            {"issuer": "https://evil.example/auth/v1"},
            {"audience": "wrong-aud"},
            {"role": "anon"},
            {"user_id": "not-a-uuid"},
        ]:
            with self.subTest(bad_kwargs=bad_kwargs):
                user_id = bad_kwargs.pop("user_id", OFFICER_1_ID)
                resp = self.client.get("/api/v1/auth/me", headers=bearer_headers(user_id, **bad_kwargs))
                self.assertEqual(resp.status_code, 401)

        # Unverified email: allowed to onboard in MVP mode, blocked when strict flag is enabled, and ALWAYS blocked from investigations
        unverified = bearer_headers(OFFICER_1_ID, email_verified=False)
        with patch.dict(os.environ, {"SUPABASE_REQUIRE_EMAIL_VERIFICATION": "true"}):
            self.assertEqual(
                self.client.put(
                    "/api/v1/verification/profile",
                    headers=unverified,
                    json={
                        "full_name": "Test Officer",
                        "designation": "Inspector",
                        "police_station": "Central",
                        "claimed_district_id": "north-division",
                    },
                ).status_code,
                403,
            )
        seed_profile(OFFICER_1_ID, verification_status="pending_review", approved_district_id=None)
        self.assertEqual(
            self.client.put(
                "/api/v1/verification/profile",
                headers=unverified,
                json={
                    "full_name": "Test Officer",
                    "designation": "Inspector",
                    "police_station": "Central",
                    "claimed_district_id": "north-division",
                },
            ).status_code,
            409,  # Reached profile handler (not blocked by email gate), rejected only because pending_review locks edits
        )
        self.assertEqual(self.client.get("/api/v1/cases", headers=unverified).status_code, 403)
        seed_profile(OFFICER_1_ID, verification_status="approved", approved_district_id="north-division")

        # Key rotation: first fetch returns old JWKS, unknown kid-2 triggers force_refresh=True
        with patch.object(
            self.verifier,
            "_fetch_jwks",
            side_effect=[test_jwks(include_rotated=False), test_jwks(include_rotated=True)],
        ) as fetch_mock:
            rotated = bearer_headers(OFFICER_1_ID, kid="test-key-2", use_rotated_key=True)
            ok = self.client.get("/api/v1/auth/me", headers=rotated)
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(any(call.kwargs.get("force_refresh") for call in fetch_mock.call_args_list))

    def test_unapproved_rejected_and_suspended_officers_are_blocked_from_investigations(self):
        for status in ("draft", "pending_review", "additional_info_required", "rejected", "suspended"):
            with self.subTest(status=status):
                seed_profile(
                    OFFICER_1_ID,
                    verification_status=status,
                    approved_district_id="north-division" if status == "suspended" else None,
                )
                headers = bearer_headers(OFFICER_1_ID)
                # /auth/me stays accessible so frontend can show status/remediation
                me = self.client.get("/api/v1/auth/me", headers=headers)
                self.assertEqual(me.status_code, 200)
                self.assertFalse(me.json()["is_approved_officer"])
                # Investigation endpoints must deny with 403
                self.assertEqual(self.client.get("/api/v1/cases", headers=headers).status_code, 403)
                self.assertEqual(
                    self.client.post(
                        "/api/v1/cases",
                        headers=headers,
                        json={"case_id": "DENY-1", "description": "Denied", "initial_zone": "North"},
                    ).status_code,
                    403,
                )

    def test_suspension_takes_effect_immediately_with_still_valid_jwt(self):
        seed_profile(OFFICER_1_ID, verification_status="approved", approved_district_id="north-division")
        seed_district_admin(ADMIN_NORTH_ID, district_id="north-division")
        officer_headers = bearer_headers(OFFICER_1_ID)
        admin_headers = bearer_headers(ADMIN_NORTH_ID)

        self.assertEqual(self.client.get("/api/v1/cases", headers=officer_headers).status_code, 200)
        suspend = self.client.post(
            f"/api/v1/admin/applications/{OFFICER_1_ID}/decision",
            headers={**admin_headers, "Idempotency-Key": "suspend-req-1"},
            json={"decision": "suspended", "reason": "Administrative audit hold"},
        )
        self.assertEqual(suspend.status_code, 200)
        self.assertEqual(suspend.json()["profile"]["verification_status"], "suspended")

        # Same exact unexpired JWT is now immediately blocked
        self.assertEqual(self.client.get("/api/v1/cases", headers=officer_headers).status_code, 403)

    def test_officer_onboarding_document_validation_and_admin_workflow(self):
        officer_headers = bearer_headers(OFFICER_1_ID, email="applicant@police.example")
        other_headers = bearer_headers(OFFICER_2_ID, email="other@police.example")
        admin_north_headers = bearer_headers(ADMIN_NORTH_ID, email="admin.north@police.example")
        admin_south_headers = bearer_headers(ADMIN_SOUTH_ID, email="admin.south@police.example")
        seed_district_admin(ADMIN_NORTH_ID, district_id="north-division")
        seed_district_admin(ADMIN_SOUTH_ID, district_id="south-division")

        # Districts list is public and seeded
        districts = self.client.get("/api/v1/districts")
        self.assertEqual(districts.status_code, 200)
        self.assertGreaterEqual(len(districts.json()["items"]), 6)

        # Save officer profile
        prof = self.client.put(
            "/api/v1/verification/profile",
            headers=officer_headers,
            json={
                "full_name": "Sub-Inspector Demo",
                "designation": "Sub-Inspector",
                "police_station": "North Gate Station",
                "claimed_district_id": "north-division",
                "employee_ref": "SYNTH-099",
            },
        )
        self.assertEqual(prof.status_code, 200)
        self.assertEqual(prof.json()["profile"]["verification_status"], "draft")

        # Cannot submit without at least 1 document
        self.assertEqual(self.client.post("/api/v1/verification/submit", headers=officer_headers).status_code, 422)

        # Reject invalid magic bytes / HTML / executable
        bad_doc = self.client.post(
            "/api/v1/verification/documents",
            headers=officer_headers,
            data={"document_type": "redacted_id"},
            files={"file": ("evil.pdf", b"<html><script>alert(1)</script></html>", "application/pdf")},
        )
        self.assertIn(bad_doc.status_code, (415, 422))

        # Upload 3 valid documents, reject 4th
        uploaded_ids: list[str] = []
        for doc_type, fname, content, mime in [
            ("redacted_id", "id.pdf", PDF_BYTES, "application/pdf"),
            ("authorization_letter", "letter.png", PNG_BYTES, "image/png"),
            ("posting_confirmation", "extra.pdf", PDF_BYTES, "application/pdf"),
        ]:
            up = self.client.post(
                "/api/v1/verification/documents",
                headers=officer_headers,
                data={"document_type": doc_type},
                files={"file": (fname, content, mime)},
            )
            self.assertEqual(up.status_code, 201, up.text)
            uploaded_ids.append(up.json()["document"]["id"])

        fourth = self.client.post(
            "/api/v1/verification/documents",
            headers=officer_headers,
            data={"document_type": "posting_confirmation"},
            files={"file": ("4th.pdf", PDF_BYTES, "application/pdf")},
        )
        self.assertEqual(fourth.status_code, 409)

        # Submit for review
        submitted = self.client.post("/api/v1/verification/submit", headers=officer_headers)
        self.assertEqual(submitted.status_code, 200)
        self.assertEqual(submitted.json()["profile"]["verification_status"], "pending_review")

        # Non-admin officer cannot access admin routes
        self.assertEqual(self.client.get("/api/v1/admin/overview", headers=officer_headers).status_code, 403)
        self.assertEqual(self.client.get("/api/v1/admin/applications", headers=other_headers).status_code, 403)

        # Cross-user document download denied; cross-district admin denied
        doc_id = uploaded_ids[0]
        self.assertEqual(
            self.client.get(f"/api/v1/verification/documents/{doc_id}/content", headers=other_headers).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(f"/api/v1/verification/documents/{doc_id}/content", headers=admin_south_headers).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(f"/api/v1/admin/applications/{OFFICER_1_ID}", headers=admin_south_headers).status_code,
            404,
        )

        # District admin in metro-north can inspect application and stream document
        detail = self.client.get(f"/api/v1/admin/applications/{OFFICER_1_ID}", headers=admin_north_headers)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(len(detail.json()["documents"]), 3)
        doc_stream = self.client.get(
            f"/api/v1/verification/documents/{doc_id}/content",
            headers=admin_north_headers,
        )
        self.assertEqual(doc_stream.status_code, 200)
        self.assertEqual(doc_stream.content, PDF_BYTES)

        # Reject/request-info/suspend require mandatory reason
        no_reason = self.client.post(
            f"/api/v1/admin/applications/{OFFICER_1_ID}/decision",
            headers={**admin_north_headers, "Idempotency-Key": "reject-no-reason"},
            json={"decision": "rejected", "reason": ""},
        )
        self.assertEqual(no_reason.status_code, 422)

        # Approve officer
        approved = self.client.post(
            f"/api/v1/admin/applications/{OFFICER_1_ID}/decision",
            headers={**admin_north_headers, "Idempotency-Key": "approve-req-1"},
            json={"decision": "approved", "reason": "Verified synthetic credentials"},
        )
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.json()["profile"]["verification_status"], "approved")
        self.assertEqual(approved.json()["profile"]["approved_district_id"], "north-division")

        # Duplicate approve with same Idempotency-Key is idempotent; conflicting reject from approved is 409
        dup_approve = self.client.post(
            f"/api/v1/admin/applications/{OFFICER_1_ID}/decision",
            headers={**admin_north_headers, "Idempotency-Key": "approve-req-1"},
            json={"decision": "approved", "reason": "Verified synthetic credentials"},
        )
        self.assertEqual(dup_approve.status_code, 200)
        self.assertTrue(dup_approve.json()["idempotent"])

        conflict = self.client.post(
            f"/api/v1/admin/applications/{OFFICER_1_ID}/decision",
            headers={**admin_north_headers, "Idempotency-Key": "reject-conflict-1"},
            json={"decision": "rejected", "reason": "Cannot reject approved directly"},
        )
        self.assertEqual(conflict.status_code, 409)

        # Approved officer can now create & view their own cases; other officer cannot see them
        created = self.client.post(
            "/api/v1/cases",
            headers=officer_headers,
            json={
                "case_id": "OWNED-1",
                "description": "Owned synthetic investigation case for Phase 3 test",
                "authorized": True,
                "initial_zone": "North",
                "neighbors": {"North": ["East"], "East": ["North"]},
            },
        )
        self.assertEqual(created.status_code, 201)
        seed_profile(OFFICER_2_ID, verification_status="approved", approved_district_id="north-division")
        self.assertEqual(self.client.get("/api/v1/cases/OWNED-1", headers=other_headers).status_code, 404)

    def test_district_admin_cannot_self_approve(self):
        seed_district_admin(ADMIN_NORTH_ID, district_id="north-division")
        seed_profile(
            ADMIN_NORTH_ID,
            verification_status="pending_review",
            claimed_district_id="north-division",
            approved_district_id=None,
        )
        admin_headers = bearer_headers(ADMIN_NORTH_ID)
        resp = self.client.post(
            f"/api/v1/admin/applications/{ADMIN_NORTH_ID}/decision",
            headers={**admin_headers, "Idempotency-Key": "self-approve-1"},
            json={"decision": "approved", "reason": "Self approval attempt"},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertIn("cannot review", resp.json()["detail"].lower())

    def test_oversized_and_active_script_verification_documents_are_rejected(self):
        seed_profile(OFFICER_1_ID, verification_status="draft", approved_district_id=None)
        headers = bearer_headers(OFFICER_1_ID)
        big = self.client.post(
            "/api/v1/verification/documents",
            headers=headers,
            data={"document_type": "redacted_id"},
            files={"file": ("big.pdf", b"%PDF-1.4\n" + b"0" * (5 * 1024 * 1024 + 32) + b"\n%%EOF\n", "application/pdf")},
        )
        self.assertEqual(big.status_code, 413)

        js_pdf = self.client.post(
            "/api/v1/verification/documents",
            headers=headers,
            data={"document_type": "redacted_id"},
            files={"file": ("active.pdf", b"%PDF-1.4\n/OpenAction /JavaScript (alert(1))\n%%EOF\n", "application/pdf")},
        )
        self.assertEqual(js_pdf.status_code, 415)

    def test_document_content_response_sets_sandbox_csp_and_verifies_integrity_hash(self):
        seed_profile(OFFICER_1_ID, verification_status="draft", approved_district_id=None)
        headers = bearer_headers(OFFICER_1_ID)
        up = self.client.post(
            "/api/v1/verification/documents",
            headers=headers,
            data={"document_type": "redacted_id"},
            files={"file": ("id.pdf", PDF_BYTES, "application/pdf")},
        )
        self.assertEqual(up.status_code, 201)
        doc_id = up.json()["document"]["id"]
        ok = self.client.get(f"/api/v1/verification/documents/{doc_id}/content", headers=headers)
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.headers.get("x-content-type-options"), "nosniff")
        self.assertEqual(ok.headers.get("content-security-policy"), "sandbox; default-src 'none'")

        # Tamper stored bytes -> integrity check fails with 500
        key = next(iter(self.storage_objects))
        self.storage_objects[key] = b"%PDF-1.4\ntampered\n%%EOF\n"
        tampered = self.client.get(f"/api/v1/verification/documents/{doc_id}/content", headers=headers)
        self.assertEqual(tampered.status_code, 500)

    def test_additional_info_resubmit_and_reinstatement_lifecycle(self):
        officer_headers = bearer_headers(OFFICER_1_ID)
        admin_headers = bearer_headers(ADMIN_NORTH_ID)
        seed_district_admin(ADMIN_NORTH_ID, district_id="north-division")
        seed_profile(OFFICER_1_ID, verification_status="pending_review", claimed_district_id="north-division", approved_district_id=None)

        req_info = self.client.post(
            f"/api/v1/admin/applications/{OFFICER_1_ID}/decision",
            headers={**admin_headers, "Idempotency-Key": "info-1"},
            json={"decision": "additional_info_required", "reason": "Upload clearer ID scan"},
        )
        self.assertEqual(req_info.status_code, 200)
        self.assertEqual(req_info.json()["profile"]["verification_status"], "additional_info_required")

        # Resubmit after adding a document
        up = self.client.post(
            "/api/v1/verification/documents",
            headers=officer_headers,
            data={"document_type": "redacted_id"},
            files={"file": ("clear.pdf", PDF_BYTES, "application/pdf")},
        )
        self.assertEqual(up.status_code, 201)
        resub = self.client.post("/api/v1/verification/submit", headers=officer_headers)
        self.assertEqual(resub.status_code, 200)
        self.assertEqual(resub.json()["profile"]["verification_status"], "pending_review")

        # Approve -> Suspend -> Reinstate
        for step_key, decision, expected_status in [
            ("app-1", "approved", "approved"),
            ("susp-1", "suspended", "suspended"),
            ("rein-1", "reinstated", "approved"),
        ]:
            resp = self.client.post(
                f"/api/v1/admin/applications/{OFFICER_1_ID}/decision",
                headers={**admin_headers, "Idempotency-Key": step_key},
                json={"decision": decision, "reason": f"Lifecycle step {decision}"},
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["profile"]["verification_status"], expected_status)

    def test_provision_admin_cli_validates_district_and_existing_user(self):
        from scripts.provision_admin import main as provision_main
        with patch("sys.argv", ["provision_admin.py", "--email", "nobody@example.com", "--district", "invalid-district"]):
            with self.assertRaises(SystemExit) as ctx:
                provision_main()
            self.assertNotEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
