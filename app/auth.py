"""Supabase Bearer JWT verification and authoritative role/approval dependencies."""
from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import jwt
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import db

logger = logging.getLogger("cctv.auth")

bearer_scheme = HTTPBearer(auto_error=False)
ALLOWED_ASYMMETRIC_ALGS = ("ES256", "RS256", "EdDSA")
JWKS_CACHE_TTL_SECONDS = 300


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: str
    email: str | None
    role: str
    email_verified: bool
    claims: dict[str, Any]


@dataclass(frozen=True)
class DistrictAdminContext:
    user: AuthenticatedUser
    district_id: str


class SupabaseJwtVerifier:
    """Verify Supabase Bearer access tokens via JWKS with controlled caching and rotation."""

    def __init__(self) -> None:
        self._jwks_cache: dict[str, Any] = {}
        self._jwks_fetched_at: float = 0.0

    def _supabase_url(self) -> str:
        url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
        if not url and os.getenv("CCTV_ENV", "").strip().lower() != "test":
            load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
            url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
        if not url:
            raise HTTPException(status_code=503, detail="Authentication provider is not configured")
        return url

    def _audience(self) -> str:
        return os.getenv("SUPABASE_JWT_AUDIENCE", "authenticated").strip() or "authenticated"

    def _issuer(self, supabase_url: str) -> str:
        return f"{supabase_url}/auth/v1"

    def _fetch_jwks(self, supabase_url: str, *, force_refresh: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if (
            not force_refresh
            and self._jwks_cache
            and (now - self._jwks_fetched_at) < JWKS_CACHE_TTL_SECONDS
        ):
            return self._jwks_cache

        jwks_url = f"{supabase_url}/auth/v1/.well-known/jwks.json"
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(jwks_url)
        except httpx.HTTPError as exc:
            logger.warning("JWKS fetch failed: %s", type(exc).__name__)
            raise HTTPException(status_code=503, detail="Authentication key service unavailable") from exc

        if resp.status_code != 200:
            raise HTTPException(status_code=503, detail="Authentication key service unavailable")

        payload = resp.json()
        keys = payload.get("keys")
        if not isinstance(keys, list):
            raise HTTPException(status_code=503, detail="Invalid JWKS payload from authentication provider")

        self._jwks_cache = payload
        self._jwks_fetched_at = now
        return payload

    def _resolve_signing_key(self, token: str, supabase_url: str) -> tuple[Any, str]:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise HTTPException(status_code=401, detail="Malformed bearer token") from exc

        alg = str(header.get("alg") or "").strip()
        if alg not in ALLOWED_ASYMMETRIC_ALGS:
            raise HTTPException(status_code=401, detail="Unsupported token signing algorithm")

        kid = str(header.get("kid") or "").strip()
        jwks = self._fetch_jwks(supabase_url, force_refresh=False)
        key_dict = self._find_jwk(jwks, kid)
        if key_dict is None:
            jwks = self._fetch_jwks(supabase_url, force_refresh=True)
            key_dict = self._find_jwk(jwks, kid)
        if key_dict is None:
            raise HTTPException(status_code=401, detail="Signing key not recognized")

        try:
            jwk_obj = jwt.PyJWK.from_dict(key_dict)
            return jwk_obj.key, alg
        except Exception as exc:
            raise HTTPException(status_code=401, detail="Invalid signing key format") from exc

    @staticmethod
    def _find_jwk(jwks: dict[str, Any], kid: str) -> dict[str, Any] | None:
        keys = jwks.get("keys") or []
        for item in keys:
            if not isinstance(item, dict):
                continue
            if not kid or item.get("kid") == kid:
                return item
        return None

    def verify_token(self, token: str) -> AuthenticatedUser:
        supabase_url = self._supabase_url()
        signing_key, alg = self._resolve_signing_key(token, supabase_url)
        issuer = self._issuer(supabase_url)
        audience = self._audience()

        try:
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=[alg],
                audience=audience,
                issuer=issuer,
                leeway=60,
                options={
                    "require": ["exp", "iat", "sub"],
                    "verify_exp": True,
                    "verify_nbf": True,
                    "verify_iat": True,
                    "verify_iss": True,
                    "verify_aud": True,
                },
            )
        except jwt.ExpiredSignatureError as exc:
            raise HTTPException(status_code=401, detail="Session token has expired") from exc
        except jwt.PyJWTError as exc:
            raise HTTPException(status_code=401, detail="Invalid authentication token") from exc

        sub = str(claims.get("sub") or "").strip()
        try:
            normalized_sub = str(uuid.UUID(sub))
        except ValueError as exc:
            raise HTTPException(status_code=401, detail="Token subject is not a valid user UUID") from exc

        role = str(claims.get("role") or "").strip()
        if role != "authenticated":
            raise HTTPException(status_code=401, detail="Authenticated user role required")

        email = claims.get("email")
        app_meta = claims.get("app_metadata") if isinstance(claims.get("app_metadata"), dict) else {}
        providers = app_meta.get("providers") if isinstance(app_meta.get("providers"), list) else []
        # Never trust user-editable user_metadata for email verification; rely on authoritative
        # top-level claim, server-controlled app_metadata, or active session amr when email
        # confirmation is enforced by Supabase Auth.
        email_verified = bool(
            claims.get("email_verified")
            or app_meta.get("provider") == "email"
            or "email" in providers
            or claims.get("amr")
        )

        return AuthenticatedUser(
            user_id=normalized_sub,
            email=str(email).strip().lower() if isinstance(email, str) and email.strip() else None,
            role=role,
            email_verified=email_verified,
            claims=claims,
        )


_verifier = SupabaseJwtVerifier()


def get_jwt_verifier() -> SupabaseJwtVerifier:
    return _verifier


def require_authenticated_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> AuthenticatedUser:
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials.strip():
        raise HTTPException(
            status_code=401,
            detail="Bearer authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = get_jwt_verifier().verify_token(credentials.credentials.strip())
    request.state.authenticated_user = user
    return user


def require_verified_email_user(
    user: AuthenticatedUser = Depends(require_authenticated_user),
) -> AuthenticatedUser:
    require_email = os.getenv("SUPABASE_REQUIRE_EMAIL_VERIFICATION", "false").strip().lower() == "true"
    if require_email and not user.email_verified:
        raise HTTPException(status_code=403, detail="Email verification is required before continuing")
    return user


def require_approved_officer(
    user: AuthenticatedUser = Depends(require_verified_email_user),
) -> AuthenticatedUser:
    db.initialize()
    with db.connection() as conn:
        profile = conn.execute(
            "SELECT verification_status, approved_district_id FROM public.profiles WHERE user_id = ?"
            if isinstance(conn, db.PostgresConnection)
            else "SELECT verification_status, approved_district_id FROM profiles WHERE user_id = ?",
            (user.user_id,),
        ).fetchone()
    if profile is None:
        raise HTTPException(
            status_code=403,
            detail="Officer verification profile is required before accessing investigations",
        )
    status = profile["verification_status"]
    if status == "suspended":
        raise HTTPException(status_code=403, detail="Officer investigation access has been suspended")
    if status == "rejected":
        raise HTTPException(status_code=403, detail="Officer verification application was rejected")
    if status != "approved":
        raise HTTPException(
            status_code=403,
            detail="Officer verification approval is pending administrative review",
        )
    return user


def require_district_admin(
    user: AuthenticatedUser = Depends(require_verified_email_user),
) -> DistrictAdminContext:
    db.initialize()
    with db.connection() as conn:
        row = conn.execute(
            "SELECT district_id, is_active FROM public.district_admins WHERE user_id = ?"
            if isinstance(conn, db.PostgresConnection)
            else "SELECT district_id, is_active FROM district_admins WHERE user_id = ?",
            (user.user_id,),
        ).fetchone()
    if row is None or not bool(row["is_active"]):
        raise HTTPException(status_code=403, detail="Active district administrator access required")
    return DistrictAdminContext(user=user, district_id=row["district_id"])


def owned_case_or_404(conn, case_id: str, owner_user_id: str):
    row = conn.execute(
        "SELECT * FROM cases WHERE case_id = ? AND owner_id = ?",
        (case_id, owner_user_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return row
