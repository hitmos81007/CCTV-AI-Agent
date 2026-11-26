"""Server-side private Supabase Storage abstraction for evidence frames."""
from __future__ import annotations

import hashlib
import os
import re
import uuid
from dataclasses import dataclass
from urllib.parse import quote

import httpx

SAFE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")
DEFAULT_EVIDENCE_BUCKET = "cctv-evidence"


class StorageConfigError(RuntimeError):
    """Raised when required server-side Supabase Storage configuration is missing."""


class UnsafeStorageKeyError(ValueError):
    """Raised when a storage key or path segment fails safety validation."""


class StorageOperationError(RuntimeError):
    """Raised when a Supabase Storage HTTP operation fails."""


def _validate_segment(value: str, label: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned or cleaned in {".", ".."} or "/" in cleaned or "\\" in cleaned or "\x00" in cleaned:
        raise UnsafeStorageKeyError(f"Unsafe {label} segment")
    if not SAFE_SEGMENT_RE.match(cleaned):
        raise UnsafeStorageKeyError(f"Invalid characters in {label} segment")
    return cleaned


def build_evidence_frame_key(
    owner_uuid: str,
    case_id: str,
    video_source_id: str,
    sighting_id: str,
    filename: str = "frame.jpg",
) -> str:
    """Build a canonical, server-generated private storage key for an evidence frame."""
    try:
        normalized_owner = str(uuid.UUID(str(owner_uuid).strip()))
    except (ValueError, TypeError) as exc:
        raise UnsafeStorageKeyError("owner_uuid must be a valid UUID") from exc

    safe_case = _validate_segment(case_id, "case_id")
    safe_video = _validate_segment(video_source_id, "video_source_id")
    safe_sighting = _validate_segment(sighting_id, "sighting_id")
    safe_file = _validate_segment(filename, "filename")
    if safe_file != "frame.jpg":
        raise UnsafeStorageKeyError("Only server-generated frame.jpg is permitted")
    return f"{normalized_owner}/{safe_case}/{safe_video}/{safe_sighting}/{safe_file}"


@dataclass(frozen=True)
class EvidenceUploadResult:
    bucket: str
    storage_key: str
    sha256: str
    byte_size: int


class SupabaseEvidenceStorage:
    """Privileged backend client for private evidence frames in Supabase Storage."""

    def __init__(
        self,
        *,
        supabase_url: str | None = None,
        service_key: str | None = None,
        bucket: str | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.supabase_url = (supabase_url if supabase_url is not None else os.getenv("SUPABASE_URL", "")).strip().rstrip("/")
        if service_key is not None:
            resolved_key = service_key.strip()
        else:
            resolved_key = (
                os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
                or os.getenv("SUPABASE_SECRET_KEY", "").strip()
            )
        self._service_key = resolved_key
        self.bucket = (bucket if bucket is not None else os.getenv("CCTV_EVIDENCE_BUCKET", DEFAULT_EVIDENCE_BUCKET)).strip() or DEFAULT_EVIDENCE_BUCKET
        self.timeout = timeout

        if not self.supabase_url or not self._service_key:
            raise StorageConfigError("SUPABASE_URL and server-only SUPABASE_SERVICE_ROLE_KEY (or SUPABASE_SECRET_KEY) are required")

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "apikey": self._service_key,
            "Authorization": f"Bearer {self._service_key}",
        }
        if extra:
            headers.update(extra)
        return headers

    def _encode_key(self, key: str) -> str:
        parts = [p for p in key.split("/") if p]
        if len(parts) < 2 or any(p in {".", ".."} for p in parts):
            raise UnsafeStorageKeyError("Invalid storage key structure")
        return "/".join(quote(part, safe="") for part in parts)

    def verify_bucket_private(self) -> dict[str, object]:
        url = f"{self.supabase_url}/storage/v1/bucket/{quote(self.bucket, safe='')}"
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(url, headers=self._headers())
        if resp.status_code != 200:
            raise StorageOperationError(f"Bucket lookup failed with status {resp.status_code}")
        data = resp.json()
        if data.get("public") is not False:
            raise StorageOperationError(f"Evidence bucket '{self.bucket}' must be private")
        return {"id": data.get("id"), "name": data.get("name"), "public": data.get("public")}

    def upload_frame(self, storage_key: str, data: bytes, *, content_type: str = "image/jpeg") -> EvidenceUploadResult:
        if not data:
            raise ValueError("Evidence frame payload cannot be empty")
        encoded_key = self._encode_key(storage_key)
        sha256 = hashlib.sha256(data).hexdigest()
        url = f"{self.supabase_url}/storage/v1/object/{quote(self.bucket, safe='')}/{encoded_key}"
        headers = self._headers({"Content-Type": content_type, "x-upsert": "false"})
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, headers=headers, content=data)
        if resp.status_code not in (200, 201):
            raise StorageOperationError(f"Evidence upload failed with status {resp.status_code}")
        return EvidenceUploadResult(
            bucket=self.bucket,
            storage_key=storage_key,
            sha256=sha256,
            byte_size=len(data),
        )

    def download_frame(self, storage_key: str) -> bytes:
        encoded_key = self._encode_key(storage_key)
        url = f"{self.supabase_url}/storage/v1/object/{quote(self.bucket, safe='')}/{encoded_key}"
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(url, headers=self._headers())
        if resp.status_code != 200:
            raise StorageOperationError(f"Evidence download failed with status {resp.status_code}")
        return resp.content

    def create_signed_url(self, storage_key: str, *, expires_in: int = 300) -> str:
        if not 1 <= expires_in <= 3600:
            raise ValueError("Signed URL expiration must be between 1 and 3600 seconds")
        encoded_key = self._encode_key(storage_key)
        url = f"{self.supabase_url}/storage/v1/object/sign/{quote(self.bucket, safe='')}/{encoded_key}"
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, headers=self._headers({"Content-Type": "application/json"}), json={"expiresIn": expires_in})
        if resp.status_code != 200:
            raise StorageOperationError(f"Signed URL creation failed with status {resp.status_code}")
        payload = resp.json()
        signed_path = payload.get("signedURL") or payload.get("signedUrl") or ""
        if not signed_path:
            raise StorageOperationError("Signed URL missing in storage response")
        if signed_path.startswith("http://") or signed_path.startswith("https://"):
            return signed_path
        if not signed_path.startswith("/storage/v1"):
            signed_path = "/storage/v1" + (signed_path if signed_path.startswith("/") else "/" + signed_path)
        return f"{self.supabase_url}{signed_path}"

    def delete_object(self, storage_key: str) -> None:
        encoded_key = self._encode_key(storage_key)
        url = f"{self.supabase_url}/storage/v1/object/{quote(self.bucket, safe='')}/{encoded_key}"
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.delete(url, headers=self._headers())
        if resp.status_code not in (200, 204, 404):
            raise StorageOperationError(f"Evidence deletion failed with status {resp.status_code}")
