"""Bounded capture and file ingestion for registered CCTV sources."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from urllib.request import url2pathname

import cv2

from . import db

ALLOWED_SUFFIXES = {".mp4", ".avi", ".mov", ".mkv"}


def _allowed_file(uri: str) -> Path:
    parsed = urlsplit(uri)
    if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
        raise ValueError("DVR export must use a local file URI")
    path = Path(url2pathname(unquote(parsed.path))).resolve(strict=True)
    roots = [Path(item).resolve() for item in os.getenv("CCTV_IMPORT_ROOTS", "").split(os.pathsep) if item]
    if not any(path.is_relative_to(root) for root in roots):
        raise PermissionError("DVR export is outside configured CCTV_IMPORT_ROOTS")
    if path.suffix.lower() not in ALLOWED_SUFFIXES or not path.is_file():
        raise ValueError("Unsupported DVR export file")
    return path


def _capture_rtsp(uri: str, destination: Path, seconds: int, camera_id: str) -> None:
    parsed = urlsplit(uri)
    allowed = {part.strip().lower() for part in os.getenv("CCTV_ALLOWED_RTSP_HOSTS", "").split(",") if part.strip()}
    if parsed.scheme not in ("rtsp", "rtsps") or not parsed.hostname or parsed.hostname.lower() not in allowed:
        raise PermissionError("RTSP host is not in CCTV_ALLOWED_RTSP_HOSTS")
    if parsed.username or parsed.password:
        raise ValueError("Store stream credentials separately")
    credential_key = "".join(ch if ch.isalnum() else "_" for ch in camera_id).upper()
    user = os.getenv("CCTV_RTSP_USER_" + credential_key)
    password = os.getenv("CCTV_RTSP_PASSWORD_" + credential_key)
    if bool(user) != bool(password):
        raise ValueError("Both stream username and password must be configured")
    if user and password:
        authority = quote(user, safe="") + ":" + quote(password, safe="") + "@" + parsed.netloc
        uri = urlunsplit((parsed.scheme, authority, parsed.path, parsed.query, parsed.fragment))
    params = [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000]
    for attempt in range(2):
        capture = cv2.VideoCapture(uri, cv2.CAP_FFMPEG, params)
        writer = None
        frames = 0
        try:
            if capture.isOpened():
                fps = capture.get(cv2.CAP_PROP_FPS)
                fps = fps if 1 <= fps <= 60 else 15
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline and frames < int(fps * seconds):
                    ok, frame = capture.read()
                    if not ok:
                        break
                    if writer is None:
                        height, width = frame.shape[:2]
                        writer = cv2.VideoWriter(str(destination), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
                        if not writer.isOpened():
                            raise ValueError("Cannot encode captured stream")
                    writer.write(frame)
                    frames += 1
        finally:
            capture.release()
            if writer is not None:
                writer.release()
        if frames:
            return
        destination.unlink(missing_ok=True)
        if attempt == 0:
            time.sleep(1)
    raise ValueError("No frames captured from authorized RTSP source")


def ingest(record: dict, seconds: int = 10) -> str:
    """Create a local, hashed video and link it to the registered source."""
    if record["video_source_id"]:
        return record["video_source_id"]
    if not record["recorded_at"]:
        raise ValueError("Source recording time is required before ingestion")
    source_type = record["source_type"]
    source = _allowed_file(record["source_uri"]) if source_type in ("dvr", "police_archive") else None
    if source_type not in ("dvr", "police_archive", "ip_camera"):
        raise ValueError("Unsupported source type")
    video_id = str(uuid.uuid4())
    destination = db.EVIDENCE_DIR / (video_id + (source.suffix.lower() if source else ".mp4"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        if source:
            shutil.copyfile(source, destination)
        else:
            _capture_rtsp(record["source_uri"], destination, seconds, record["camera_id"])
        capture = cv2.VideoCapture(str(destination))
        try:
            if not capture.isOpened():
                raise ValueError("Imported recording cannot be decoded")
            fps = capture.get(cv2.CAP_PROP_FPS)
            count = capture.get(cv2.CAP_PROP_FRAME_COUNT)
            ok, _ = capture.read()
            if not ok or fps <= 0:
                raise ValueError("Imported recording has no readable video frames")
            duration = count / fps
        finally:
            capture.release()
        digest = hashlib.sha256()
        with destination.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        metadata = {"probe": "opencv", "fps": fps, "frame_count": count, "duration_seconds": duration}
        ffprobe = os.getenv("FFPROBE_PATH") or shutil.which("ffprobe")
        if ffprobe:
            try:
                result = subprocess.run([ffprobe, "-v", "error", "-show_streams", "-show_format",
                                         "-of", "json", str(destination)], capture_output=True, text=True,
                                        timeout=30, check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                metadata = {"probe": "ffprobe", "data": json.loads(result.stdout)}
            except (OSError, subprocess.SubprocessError, ValueError):
                raise ValueError("FFprobe could not inspect the imported recording")
        with db.connection() as conn:
            current = conn.execute("SELECT video_source_id FROM recordings WHERE id=?", (record["id"],)).fetchone()
            if not current or current["video_source_id"]:
                raise ValueError("Source already ingested or no longer exists")
            conn.execute("INSERT INTO videos(video_source_id,case_id,jurisdiction,camera_id,original_name,file_path,recorded_at,sha256,byte_size,duration_seconds,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                         (video_id, record["case_id"], record["jurisdiction"], record["camera_id"],
                          source.name if source else "rtsp-capture.mp4", str(destination), record["recorded_at"],
                          digest.hexdigest(), destination.stat().st_size, duration, db.utc_now()))
            changed = conn.execute("UPDATE recordings SET video_source_id=?,status='ready' WHERE id=? AND video_source_id IS NULL",
                                   (video_id, record["id"]))
            if changed.rowcount != 1:
                raise ValueError("Source was ingested concurrently")
            conn.execute("INSERT INTO video_metadata(video_source_id,metadata_json) VALUES(?,?)",
                         (video_id, db.canonical_json(metadata)))
            db.audit(conn, "source_ingested", {"recording_id": record["id"], "video_source_id": video_id, "sha256": digest.hexdigest()})
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return video_id
