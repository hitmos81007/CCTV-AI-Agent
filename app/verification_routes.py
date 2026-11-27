"""Officer verification onboarding and district administrator review API routes."""
from __future__ import annotations

import hashlib
import os
import re
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from . import db
from .auth import (
    AuthenticatedUser,
    DistrictAdminContext,
    require_authenticated_user,
    require_district_admin,
    require_verified_email_user,
)
from .storage import (
    StorageConfigError,
    StorageOperationError,
    SupabaseVerificationStorage,
    build_verification_document_key,
    validate_verification_document_bytes,
    verification_max_bytes,
)

router = APIRouter(prefix="/api/v1", tags=["verification"])

ALLOWED_DOC_TYPES = {"redacted_id", "authorization_letter", "posting_confirmation"}
MAX_DOCS_PER_SUBMISSION = 3
EDITABLE_PROFILE_STATUSES = {"draft", "additional_info_required"}
SUBMITTABLE_STATUSES = {"draft", "additional_info_required"}

VALID_ADMIN_TRANSITIONS: dict[tuple[str, str], str] = {
    ("pending_review", "approved"): "approved",
    ("additional_info_required", "approved"): "approved",
    ("pending_review", "rejected"): "rejected",
    ("additional_info_required", "rejected"): "rejected",
    ("pending_review", "additional_info_required"): "additional_info_required",
    ("approved", "suspended"): "suspended",
    ("suspended", "reinstated"): "approved",
}

DECISIONS_REQUIRING_REASON = {"rejected", "additional_info_required", "suspended"}


class OfficerProfileUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    full_name: str = Field(min_length=2, max_length=120)
    designation: str = Field(min_length=2, max_length=120)
    police_station: str = Field(min_length=2, max_length=160)
    claimed_district_id: str = Field(min_length=2, max_length=80)
    employee_ref: str | None = Field(default=None, max_length=64)


class AdminDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: str = Field(
        pattern="^(approved|rejected|additional_info_required|suspended|reinstated)$"
    )
    reason: str | None = Field(default=None, max_length=1000)
    request_key: str | None = Field(default=None, min_length=4, max_length=128)


def _tbl(conn, name: str) -> str:
    return f"public.{name}" if isinstance(conn, db.PostgresConnection) else name


def _serialize_profile(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "user_id": str(row["user_id"]),
        "full_name": row["full_name"],
        "designation": row["designation"],
        "police_station": row["police_station"],
        "claimed_district_id": row["claimed_district_id"],
        "approved_district_id": row["approved_district_id"],
        "employee_ref": row["employee_ref"],
        "verification_status": row["verification_status"],
        "status_reason": row["status_reason"],
        "submission_version": int(row["submission_version"] or 1),
        "submitted_at": str(row["submitted_at"]) if row["submitted_at"] else None,
        "approved_at": str(row["approved_at"]) if row["approved_at"] else None,
        "updated_at": str(row["updated_at"]) if row["updated_at"] else None,
    }


def _serialize_document(row: Any) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "document_type": row["document_type"],
        "original_filename": row["original_filename"],
        "mime_type": row["mime_type"],
        "byte_size": int(row["byte_size"]),
        "sha256": row["sha256"],
        "submission_version": int(row["submission_version"] or 1),
        "uploaded_at": str(row["uploaded_at"]),
    }


def _serialize_review(row: Any) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "applicant_user_id": str(row["applicant_user_id"]),
        "admin_user_id": str(row["admin_user_id"]),
        "district_id": row["district_id"],
        "decision": row["decision"],
        "previous_status": row["previous_status"],
        "new_status": row["new_status"],
        "reason": row["reason"],
        "created_at": str(row["created_at"]),
    }


def _store_verification_bytes(storage_key: str, data: bytes, mime_type: str) -> None:
    if db.runtime_env() == "test" and not os.getenv("SUPABASE_URL", "").strip():
        local_path = db.EVIDENCE_DIR / "officer_verification" / storage_key
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(data)
        return
    try:
        storage = SupabaseVerificationStorage()
        storage.upload_document(storage_key, data, content_type=mime_type)
    except (StorageConfigError, StorageOperationError) as exc:
        raise HTTPException(status_code=503, detail="Verification document storage unavailable") from exc


def _load_verification_bytes(storage_key: str) -> bytes:
    if db.runtime_env() == "test" and not os.getenv("SUPABASE_URL", "").strip():
        local_path = db.EVIDENCE_DIR / "officer_verification" / storage_key
        if not local_path.is_file():
            raise HTTPException(status_code=404, detail="Verification document content not found")
        return local_path.read_bytes()
    try:
        storage = SupabaseVerificationStorage()
        return storage.download_document(storage_key)
    except StorageOperationError as exc:
        raise HTTPException(status_code=404, detail="Verification document content not found") from exc
    except StorageConfigError as exc:
        raise HTTPException(status_code=503, detail="Verification document storage unavailable") from exc


def _delete_verification_bytes(storage_key: str) -> None:
    if db.runtime_env() == "test" and not os.getenv("SUPABASE_URL", "").strip():
        local_path = db.EVIDENCE_DIR / "officer_verification" / storage_key
        local_path.unlink(missing_ok=True)
        return
    try:
        SupabaseVerificationStorage().delete_object(storage_key)
    except Exception:
        pass


@router.get("/districts")
def list_districts():
    db.initialize()
    with db.connection() as conn:
        rows = conn.execute(
            f"SELECT id, name FROM {_tbl(conn, 'districts')} WHERE is_active = ? ORDER BY name",
            (True if isinstance(conn, db.PostgresConnection) else 1,),
        ).fetchall()
    return {"items": [{"id": r["id"], "name": r["name"]} for r in rows]}


@router.get("/auth/me")
def auth_me(user: AuthenticatedUser = Depends(require_authenticated_user)):
    db.initialize()
    with db.connection() as conn:
        profile_row = conn.execute(
            f"SELECT * FROM {_tbl(conn, 'profiles')} WHERE user_id = ?",
            (user.user_id,),
        ).fetchone()
        admin_row = conn.execute(
            f"SELECT district_id, is_active FROM {_tbl(conn, 'district_admins')} WHERE user_id = ?",
            (user.user_id,),
        ).fetchone()
        docs = conn.execute(
            f"SELECT id, document_type, original_filename, mime_type, byte_size, sha256, submission_version, uploaded_at "
            f"FROM {_tbl(conn, 'verification_documents')} WHERE applicant_user_id = ? ORDER BY uploaded_at DESC",
            (user.user_id,),
        ).fetchall()
        reviews = conn.execute(
            f"SELECT id, applicant_user_id, admin_user_id, district_id, decision, previous_status, new_status, reason, created_at "
            f"FROM {_tbl(conn, 'verification_reviews')} WHERE applicant_user_id = ? ORDER BY created_at DESC",
            (user.user_id,),
        ).fetchall()

    profile = _serialize_profile(profile_row)
    is_admin = bool(admin_row and admin_row["is_active"])
    is_approved = bool(profile and profile["verification_status"] == "approved")
    return {
        "user_id": user.user_id,
        "email": user.email,
        "email_verified": user.email_verified,
        "is_approved_officer": is_approved,
        "is_district_admin": is_admin,
        "admin_district_id": admin_row["district_id"] if is_admin else None,
        "profile": profile,
        "documents": [_serialize_document(d) for d in docs],
        "reviews": [_serialize_review(r) for r in reviews],
    }


@router.put("/verification/profile")
def upsert_officer_profile(
    payload: OfficerProfileUpsert,
    user: AuthenticatedUser = Depends(require_verified_email_user),
):
    db.initialize()
    now = db.utc_now()
    with db.connection() as conn:
        district = conn.execute(
            f"SELECT id, is_active FROM {_tbl(conn, 'districts')} WHERE id = ?",
            (payload.claimed_district_id,),
        ).fetchone()
        if district is None or not bool(district["is_active"]):
            raise HTTPException(status_code=422, detail="Selected district is not active or supported")

        existing = conn.execute(
            f"SELECT * FROM {_tbl(conn, 'profiles')} WHERE user_id = ?",
            (user.user_id,),
        ).fetchone()
        if existing is not None and existing["verification_status"] not in EDITABLE_PROFILE_STATUSES:
            raise HTTPException(
                status_code=409,
                detail="Profile cannot be edited in its current verification status",
            )

        if existing is None:
            conn.execute(
                f"INSERT INTO {_tbl(conn, 'profiles')}("
                "user_id, full_name, designation, police_station, claimed_district_id, "
                "employee_ref, verification_status, submission_version, updated_at"
                ") VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    user.user_id,
                    payload.full_name,
                    payload.designation,
                    payload.police_station,
                    payload.claimed_district_id,
                    payload.employee_ref,
                    "draft",
                    1,
                    now,
                ),
            )
        else:
            conn.execute(
                f"UPDATE {_tbl(conn, 'profiles')} "
                "SET full_name = ?, designation = ?, police_station = ?, "
                "claimed_district_id = ?, employee_ref = ?, updated_at = ? "
                "WHERE user_id = ?",
                (
                    payload.full_name,
                    payload.designation,
                    payload.police_station,
                    payload.claimed_district_id,
                    payload.employee_ref,
                    now,
                    user.user_id,
                ),
            )

        updated = conn.execute(
            f"SELECT * FROM {_tbl(conn, 'profiles')} WHERE user_id = ?",
            (user.user_id,),
        ).fetchone()
        db.audit(
            conn,
            "officer_profile_saved",
            {"user_id": user.user_id, "claimed_district_id": payload.claimed_district_id},
        )
    return {"profile": _serialize_profile(updated)}


@router.post("/verification/documents", status_code=201)
async def upload_verification_document(
    document_type: str = Form(...),
    file: UploadFile = File(...),
    user: AuthenticatedUser = Depends(require_verified_email_user),
):
    if document_type not in ALLOWED_DOC_TYPES:
        raise HTTPException(status_code=422, detail="Unsupported verification document type")

    limit = verification_max_bytes()
    data = await file.read(limit + 1)
    await file.close()
    try:
        verified_mime, extension = validate_verification_document_bytes(data, file.content_type)
    except ValueError as exc:
        msg = str(exc)
        status_code = 413 if "exceeds maximum" in msg else 415
        raise HTTPException(status_code=status_code, detail=msg) from exc

    db.initialize()
    doc_id = str(uuid.uuid4())
    now = db.utc_now()
    safe_name = Path(file.filename or f"document{extension}").name[:120]

    with db.connection() as conn:
        profile = conn.execute(
            f"SELECT verification_status, submission_version FROM {_tbl(conn, 'profiles')} WHERE user_id = ?",
            (user.user_id,),
        ).fetchone()
        if profile is None:
            raise HTTPException(status_code=409, detail="Save professional details before uploading documents")
        if profile["verification_status"] not in EDITABLE_PROFILE_STATUSES:
            raise HTTPException(
                status_code=409,
                detail="Documents cannot be uploaded in the current verification status",
            )
        version = int(profile["submission_version"] or 1)
        existing_count = conn.execute(
            f"SELECT COUNT(*) AS total FROM {_tbl(conn, 'verification_documents')} "
            "WHERE applicant_user_id = ? AND submission_version = ?",
            (user.user_id, version),
        ).fetchone()["total"]
        if int(existing_count) >= MAX_DOCS_PER_SUBMISSION:
            raise HTTPException(
                status_code=409,
                detail=f"Maximum of {MAX_DOCS_PER_SUBMISSION} documents allowed per submission",
            )

        storage_key = build_verification_document_key(user.user_id, version, doc_id, extension)

    _store_verification_bytes(storage_key, data, verified_mime)

    import hashlib

    sha256 = hashlib.sha256(data).hexdigest()
    try:
        with db.connection() as conn:
            conn.execute(
                f"INSERT INTO {_tbl(conn, 'verification_documents')}("
                "id, applicant_user_id, document_type, storage_key, original_filename, "
                "mime_type, byte_size, sha256, submission_version, uploaded_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    doc_id,
                    user.user_id,
                    document_type,
                    storage_key,
                    safe_name,
                    verified_mime,
                    len(data),
                    sha256,
                    version,
                    now,
                ),
            )
            row = conn.execute(
                f"SELECT * FROM {_tbl(conn, 'verification_documents')} WHERE id = ?",
                (doc_id,),
            ).fetchone()
            db.audit(
                conn,
                "verification_document_uploaded",
                {"user_id": user.user_id, "document_id": doc_id, "sha256": sha256},
            )
    except Exception:
        _delete_verification_bytes(storage_key)
        raise

    return {"document": _serialize_document(row)}


@router.delete("/verification/documents/{doc_id}")
def delete_verification_document(
    doc_id: str,
    user: AuthenticatedUser = Depends(require_verified_email_user),
):
    db.initialize()
    with db.connection() as conn:
        doc = conn.execute(
            f"SELECT * FROM {_tbl(conn, 'verification_documents')} WHERE id = ? AND applicant_user_id = ?",
            (doc_id, user.user_id),
        ).fetchone()
        if doc is None:
            raise HTTPException(status_code=404, detail="Verification document not found")
        profile = conn.execute(
            f"SELECT verification_status FROM {_tbl(conn, 'profiles')} WHERE user_id = ?",
            (user.user_id,),
        ).fetchone()
        if profile is None or profile["verification_status"] not in EDITABLE_PROFILE_STATUSES:
            raise HTTPException(status_code=409, detail="Documents cannot be removed after submission")
        conn.execute(
            f"DELETE FROM {_tbl(conn, 'verification_documents')} WHERE id = ? AND applicant_user_id = ?",
            (doc_id, user.user_id),
        )
    _delete_verification_bytes(doc["storage_key"])
    return {"deleted": True, "id": doc_id}


@router.post("/verification/submit")
def submit_verification_application(user: AuthenticatedUser = Depends(require_verified_email_user)):
    db.initialize()
    now = db.utc_now()
    with db.connection() as conn:
        profile = conn.execute(
            f"SELECT * FROM {_tbl(conn, 'profiles')} WHERE user_id = ?",
            (user.user_id,),
        ).fetchone()
        if profile is None:
            raise HTTPException(status_code=422, detail="Complete professional profile before submitting")
        if profile["verification_status"] not in SUBMITTABLE_STATUSES:
            raise HTTPException(
                status_code=409,
                detail="Application cannot be submitted in its current verification status",
            )
        version = int(profile["submission_version"] or 1)
        doc_count = conn.execute(
            f"SELECT COUNT(*) AS total FROM {_tbl(conn, 'verification_documents')} "
            "WHERE applicant_user_id = ? AND submission_version = ?",
            (user.user_id, version),
        ).fetchone()["total"]
        if int(doc_count) < 1:
            raise HTTPException(
                status_code=422,
                detail="Upload at least one supporting credential document before submitting",
            )
        updated = conn.execute(
            f"UPDATE {_tbl(conn, 'profiles')} "
            "SET verification_status = 'pending_review', status_reason = NULL, submitted_at = ?, updated_at = ? "
            "WHERE user_id = ? AND verification_status = ?",
            (now, now, user.user_id, profile["verification_status"]),
        )
        if updated.rowcount != 1:
            raise HTTPException(status_code=409, detail="Concurrent application update detected")
        conn.execute(
            f"INSERT INTO {_tbl(conn, 'verification_events')}(user_id, district_id, kind, created_at) "
            "VALUES(?,?,?,?)",
            (user.user_id, profile["claimed_district_id"], "application_submitted", now),
        )
        refreshed = conn.execute(
            f"SELECT * FROM {_tbl(conn, 'profiles')} WHERE user_id = ?",
            (user.user_id,),
        ).fetchone()
        db.audit(
            conn,
            "verification_application_submitted",
            {"user_id": user.user_id, "district_id": profile["claimed_district_id"], "version": version},
        )
    return {"profile": _serialize_profile(refreshed)}


@router.get("/verification/documents/{doc_id}/content")
def get_verification_document_content(
    doc_id: str,
    user: AuthenticatedUser = Depends(require_verified_email_user),
):
    db.initialize()
    with db.connection() as conn:
        doc = conn.execute(
            f"SELECT d.*, p.claimed_district_id FROM {_tbl(conn, 'verification_documents')} d "
            f"JOIN {_tbl(conn, 'profiles')} p ON p.user_id = d.applicant_user_id "
            "WHERE d.id = ?",
            (doc_id,),
        ).fetchone()
        if doc is None:
            raise HTTPException(status_code=404, detail="Verification document not found")

        is_owner = str(doc["applicant_user_id"]) == user.user_id
        is_authorized_admin = False
        if not is_owner:
            admin_row = conn.execute(
                f"SELECT district_id, is_active FROM {_tbl(conn, 'district_admins')} WHERE user_id = ?",
                (user.user_id,),
            ).fetchone()
            is_authorized_admin = bool(
                admin_row and admin_row["is_active"] and admin_row["district_id"] == doc["claimed_district_id"]
            )
        if not (is_owner or is_authorized_admin):
            raise HTTPException(status_code=404, detail="Verification document not found")

    content = _load_verification_bytes(doc["storage_key"])
    if hashlib.sha256(content).hexdigest() != doc["sha256"]:
        raise HTTPException(status_code=500, detail="Stored verification document failed integrity hash check")
    safe_name = re.sub(r'[^A-Za-z0-9._-]+', '_', Path(doc["original_filename"]).name)[:120] or "document"
    return Response(
        content=content,
        media_type=doc["mime_type"],
        headers={
            "Content-Disposition": f'inline; filename="{safe_name}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'",
        },
    )


@router.get("/admin/overview")
def admin_overview(admin: DistrictAdminContext = Depends(require_district_admin)):
    db.initialize()
    with db.connection() as conn:
        rows = conn.execute(
            f"SELECT verification_status, COUNT(*) AS total FROM {_tbl(conn, 'profiles')} "
            "WHERE claimed_district_id = ? GROUP BY verification_status",
            (admin.district_id,),
        ).fetchall()
        review_count = conn.execute(
            f"SELECT COUNT(*) AS total FROM {_tbl(conn, 'verification_reviews')} WHERE district_id = ?",
            (admin.district_id,),
        ).fetchone()["total"]
    counts = {r["verification_status"]: int(r["total"]) for r in rows}
    return {
        "district_id": admin.district_id,
        "counts": {
            "pending_review": counts.get("pending_review", 0),
            "additional_info_required": counts.get("additional_info_required", 0),
            "approved": counts.get("approved", 0),
            "rejected": counts.get("rejected", 0),
            "suspended": counts.get("suspended", 0),
            "draft": counts.get("draft", 0),
        },
        "total_reviews": int(review_count),
    }


@router.get("/admin/applications")
def admin_list_applications(
    status: str | None = None,
    admin: DistrictAdminContext = Depends(require_district_admin),
):
    db.initialize()
    with db.connection() as conn:
        sql = f"SELECT * FROM {_tbl(conn, 'profiles')} WHERE claimed_district_id = ?"
        params: list[Any] = [admin.district_id]
        if status:
            sql += " AND verification_status = ?"
            params.append(status)
        sql += " ORDER BY updated_at DESC"
        rows = conn.execute(sql, params).fetchall()
        items = []
        for row in rows:
            doc_count = conn.execute(
                f"SELECT COUNT(*) AS total FROM {_tbl(conn, 'verification_documents')} WHERE applicant_user_id = ?",
                (row["user_id"],),
            ).fetchone()["total"]
            item = _serialize_profile(row)
            item["document_count"] = int(doc_count)
            items.append(item)
    return {"district_id": admin.district_id, "items": items}


@router.get("/admin/applications/{applicant_user_id}")
def admin_get_application(
    applicant_user_id: str,
    admin: DistrictAdminContext = Depends(require_district_admin),
):
    db.initialize()
    with db.connection() as conn:
        profile = conn.execute(
            f"SELECT * FROM {_tbl(conn, 'profiles')} WHERE user_id = ? AND claimed_district_id = ?",
            (applicant_user_id, admin.district_id),
        ).fetchone()
        if profile is None:
            raise HTTPException(status_code=404, detail="Application not found in your district")
        docs = conn.execute(
            f"SELECT * FROM {_tbl(conn, 'verification_documents')} WHERE applicant_user_id = ? ORDER BY uploaded_at DESC",
            (applicant_user_id,),
        ).fetchall()
        reviews = conn.execute(
            f"SELECT * FROM {_tbl(conn, 'verification_reviews')} WHERE applicant_user_id = ? ORDER BY created_at DESC",
            (applicant_user_id,),
        ).fetchall()
    return {
        "profile": _serialize_profile(profile),
        "documents": [_serialize_document(d) for d in docs],
        "reviews": [_serialize_review(r) for r in reviews],
    }


@router.post("/admin/applications/{applicant_user_id}/decision")
def admin_decide_application(
    applicant_user_id: str,
    payload: AdminDecisionRequest,
    idempotency_key: str | None = Header(default=None, max_length=128),
    admin: DistrictAdminContext = Depends(require_district_admin),
):
    if applicant_user_id == admin.user.user_id:
        raise HTTPException(status_code=403, detail="Administrators cannot review or approve their own account")

    req_key = (idempotency_key or payload.request_key or "").strip()
    if not req_key:
        raise HTTPException(status_code=422, detail="Idempotency-Key or request_key is required")

    reason = (payload.reason or "").strip() or None
    if payload.decision in DECISIONS_REQUIRING_REASON and not reason:
        raise HTTPException(status_code=422, detail=f"A reason is required for '{payload.decision}'")

    db.initialize()
    now = db.utc_now()
    with db.connection() as conn:
        existing_review = conn.execute(
            f"SELECT * FROM {_tbl(conn, 'verification_reviews')} WHERE applicant_user_id = ? AND request_key = ?",
            (applicant_user_id, req_key),
        ).fetchone()
        if existing_review is not None:
            profile = conn.execute(
                f"SELECT * FROM {_tbl(conn, 'profiles')} WHERE user_id = ?",
                (applicant_user_id,),
            ).fetchone()
            return {"profile": _serialize_profile(profile), "review": _serialize_review(existing_review), "idempotent": True}

        profile = conn.execute(
            f"SELECT * FROM {_tbl(conn, 'profiles')} WHERE user_id = ? AND claimed_district_id = ?",
            (applicant_user_id, admin.district_id),
        ).fetchone()
        if profile is None:
            raise HTTPException(status_code=404, detail="Application not found in your district")

        prev_status = profile["verification_status"]
        next_status = VALID_ADMIN_TRANSITIONS.get((prev_status, payload.decision))
        if next_status is None:
            raise HTTPException(
                status_code=409,
                detail=f"Invalid verification transition from '{prev_status}' via '{payload.decision}'",
            )

        approved_district = admin.district_id if next_status == "approved" else profile["approved_district_id"]
        approved_at = now if (next_status == "approved" and not profile["approved_at"]) else profile["approved_at"]
        next_version = (
            int(profile["submission_version"] or 1) + 1
            if payload.decision == "additional_info_required"
            else int(profile["submission_version"] or 1)
        )

        updated = conn.execute(
            f"UPDATE {_tbl(conn, 'profiles')} "
            "SET verification_status = ?, approved_district_id = ?, status_reason = ?, "
            "approved_at = ?, submission_version = ?, updated_at = ? "
            "WHERE user_id = ? AND verification_status = ?",
            (
                next_status,
                approved_district,
                reason,
                approved_at,
                next_version,
                now,
                applicant_user_id,
                prev_status,
            ),
        )
        if updated.rowcount != 1:
            raise HTTPException(status_code=409, detail="Application status changed concurrently; refresh and retry")

        review_id = str(uuid.uuid4())
        conn.execute(
            f"INSERT INTO {_tbl(conn, 'verification_reviews')}("
            "id, applicant_user_id, admin_user_id, district_id, decision, "
            "previous_status, new_status, reason, request_key, created_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                review_id,
                applicant_user_id,
                admin.user.user_id,
                admin.district_id,
                payload.decision,
                prev_status,
                next_status,
                reason,
                req_key,
                now,
            ),
        )
        conn.execute(
            f"INSERT INTO {_tbl(conn, 'verification_events')}(user_id, district_id, kind, created_at) "
            "VALUES(?,?,?,?)",
            (applicant_user_id, admin.district_id, "status_changed", now),
        )
        db.audit(
            conn,
            "officer_verification_decided",
            {
                "applicant_user_id": applicant_user_id,
                "admin_user_id": admin.user.user_id,
                "district_id": admin.district_id,
                "decision": payload.decision,
                "previous_status": prev_status,
                "new_status": next_status,
            },
        )
        refreshed = conn.execute(
            f"SELECT * FROM {_tbl(conn, 'profiles')} WHERE user_id = ?",
            (applicant_user_id,),
        ).fetchone()
        review = conn.execute(
            f"SELECT * FROM {_tbl(conn, 'verification_reviews')} WHERE id = ?",
            (review_id,),
        ).fetchone()
    return {"profile": _serialize_profile(refreshed), "review": _serialize_review(review), "idempotent": False}


@router.get("/admin/history")
def admin_history(admin: DistrictAdminContext = Depends(require_district_admin)):
    db.initialize()
    with db.connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM {_tbl(conn, 'verification_reviews')} WHERE district_id = ? ORDER BY created_at DESC LIMIT 100",
            (admin.district_id,),
        ).fetchall()
    return {"district_id": admin.district_id, "items": [_serialize_review(r) for r in rows]}
