from __future__ import annotations

import json
import time
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import ec

from app import db

TEST_SUPABASE_URL = "https://test-project.supabase.co"
TEST_ISSUER = f"{TEST_SUPABASE_URL}/auth/v1"
TEST_AUDIENCE = "authenticated"

OFFICER_1_ID = "11111111-1111-4111-8111-111111111111"
OFFICER_2_ID = "22222222-2222-4222-8222-222222222222"
ADMIN_NORTH_ID = "33333333-3333-4333-8333-333333333333"
ADMIN_SOUTH_ID = "44444444-4444-4444-8444-444444444444"

_PRIVATE_KEY_1 = ec.generate_private_key(ec.SECP256R1())
_PUBLIC_JWK_1 = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(_PRIVATE_KEY_1.public_key()))
_PUBLIC_JWK_1["kid"] = "test-key-1"
_PUBLIC_JWK_1["use"] = "sig"
_PUBLIC_JWK_1["alg"] = "ES256"

_PRIVATE_KEY_2 = ec.generate_private_key(ec.SECP256R1())
_PUBLIC_JWK_2 = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(_PRIVATE_KEY_2.public_key()))
_PUBLIC_JWK_2["kid"] = "test-key-2"
_PUBLIC_JWK_2["use"] = "sig"
_PUBLIC_JWK_2["alg"] = "ES256"


def test_jwks(include_rotated: bool = False) -> dict[str, Any]:
    keys = [_PUBLIC_JWK_1]
    if include_rotated:
        keys.append(_PUBLIC_JWK_2)
    return {"keys": keys}


def mint_supabase_jwt(
    user_id: str = OFFICER_1_ID,
    *,
    email: str = "officer@police.example",
    email_verified: bool = True,
    role: str = "authenticated",
    audience: str = TEST_AUDIENCE,
    issuer: str = TEST_ISSUER,
    exp_delta_seconds: int = 3600,
    alg: str = "ES256",
    kid: str = "test-key-1",
    use_rotated_key: bool = False,
) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": user_id,
        "email": email,
        "role": role,
        "aud": audience,
        "iss": issuer,
        "iat": now - 5,
        "nbf": now - 5,
        "exp": now + exp_delta_seconds,
        "email_verified": email_verified,
        "user_metadata": {"email_verified": email_verified},
    }
    if email_verified:
        payload["amr"] = [{"method": "password", "timestamp": now - 5}]
    key: Any = _PRIVATE_KEY_2 if use_rotated_key else _PRIVATE_KEY_1
    if alg == "HS256":
        key = "not-an-asymmetric-secret-at-all-0123456789abcdef"
    return jwt.encode(payload, key, algorithm=alg, headers={"kid": kid})


def bearer_headers(user_id: str = OFFICER_1_ID, **kwargs: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_supabase_jwt(user_id, **kwargs)}"}


def seed_profile(
    user_id: str = OFFICER_1_ID,
    *,
    full_name: str = "Inspector Test Officer",
    designation: str = "Inspector",
    police_station: str = "North Central Precinct",
    claimed_district_id: str = "north-division",
    approved_district_id: str | None = "north-division",
    verification_status: str = "approved",
    employee_ref: str = "SYNTH-OFF-001",
    submission_version: int = 1,
) -> None:
    db.initialize()
    now = db.utc_now()
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO profiles(
                user_id, full_name, designation, police_station,
                claimed_district_id, approved_district_id, employee_ref,
                verification_status, submission_version, submitted_at, approved_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
                full_name=excluded.full_name,
                designation=excluded.designation,
                police_station=excluded.police_station,
                claimed_district_id=excluded.claimed_district_id,
                approved_district_id=excluded.approved_district_id,
                employee_ref=excluded.employee_ref,
                verification_status=excluded.verification_status,
                submission_version=excluded.submission_version,
                updated_at=excluded.updated_at
            """,
            (
                user_id,
                full_name,
                designation,
                police_station,
                claimed_district_id,
                approved_district_id,
                employee_ref,
                verification_status,
                submission_version,
                now if verification_status != "draft" else None,
                now if verification_status == "approved" else None,
                now,
            ),
        )


def seed_district_admin(
    user_id: str = ADMIN_NORTH_ID,
    *,
    district_id: str = "north-division",
    is_active: bool = True,
) -> None:
    db.initialize()
    now = db.utc_now()
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO district_admins(user_id, district_id, is_active, provisioned_at, updated_at)
            VALUES(?,?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
                district_id=excluded.district_id,
                is_active=excluded.is_active,
                updated_at=excluded.updated_at
            """,
            (user_id, district_id, 1 if is_active else 0, now, now),
        )
