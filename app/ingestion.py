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

ALLOWED_SUFFIXES = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v"}


def resolve_ffprobe_path() -> str | None:
    """Locate ffprobe from FFPROBE_PATH, PATH, or local user FFmpeg install."""
    configured = os.getenv("FFPROBE_PATH", "").strip()
    if configured and Path(configured).is_file():
        return configured
    found = shutil.which("ffprobe")
    if found:
        return found
    local_app = os.getenv("LOCALAPPDATA", "").strip()
    if local_app:
        candidates = list((Path(local_app) / "ffmpeg").rglob("ffprobe.exe"))
        if candidates:
            return str(candidates[0])
    return None


def validate_video_container_header(path: Path, suffix: str) -> None:
    """Reject non-video files masquerading with a video extension."""
    with path.open("rb") as handle:
        header = handle.read(64)
    if len(header) < 12:
        raise ValueError("Uploaded file is too small to be a valid video")
    ext = suffix.lower()
    if ext in {".mp4", ".mov", ".m4v"}:
        if b"ftyp" not in header[:32] and header[4:8] not in {b"moov", b"mdat", b"wide", b"free"}:
            raise ValueError("Uploaded file does not have a valid MP4/MOV container signature")
    elif ext == ".avi":
        if not (header.startswith(b"RIFF") and header[8:12] == b"AVI "):
            raise ValueError("Uploaded file does not have a valid AVI container signature")
    elif ext in {".mkv", ".webm"}:
        if not header.startswith(b"\x1a\x45\xdf\xa3"):
            raise ValueError("Uploaded file does not have a valid Matroska/WebM container signature")


def validate_and_probe_uploaded_video(path: Path, suffix: str) -> dict:
    """Validate container signature, decode a frame with OpenCV, and extract metadata."""
    validate_video_container_header(path, suffix)
    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            raise ValueError("Uploaded file could not be opened as a video")
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = float(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        ok, frame = capture.read()
        if not ok or frame is None:
            raise ValueError("Uploaded video has no readable video frames")
        if width <= 0 or height <= 0:
            height, width = frame.shape[:2]
        duration = float(frame_count / fps) if fps > 0 and frame_count > 0 else 0.0
    finally:
        capture.release()

    codec: str | None = None
    probe_payload: dict = {
        "probe": "opencv",
        "fps": fps,
        "frame_count": frame_count,
        "width": width,
        "height": height,
        "duration_seconds": duration,
    }
    ffprobe = resolve_ffprobe_path()
    if ffprobe:
        try:
            result = subprocess.run(
                [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            parsed = json.loads(result.stdout)
            video_streams = [s for s in parsed.get("streams", []) if s.get("codec_type") == "video"]
            if not video_streams:
                raise ValueError("Uploaded file contains no video stream")
            vs = video_streams[0]
            width = int(vs.get("width") or width)
            height = int(vs.get("height") or height)
            codec = str(vs.get("codec_name") or "") or None
            rate_raw = str(vs.get("avg_frame_rate") or vs.get("r_frame_rate") or "")
            if "/" in rate_raw:
                num, den = rate_raw.split("/", 1)
                if float(den) > 0:
                    fps = float(num) / float(den)
            fmt_dur = parsed.get("format", {}).get("duration") or vs.get("duration")
            if fmt_dur is not None:
                duration = float(fmt_dur)
            probe_payload = {"probe": "ffprobe", "data": parsed}
        except ValueError:
            raise
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError("FFprobe could not inspect the uploaded video") from exc

    return {
        "duration_seconds": round(max(0.0, duration), 3),
        "width": width or None,
        "height": height or None,
        "fps": round(fps, 3) if fps > 0 else None,
        "codec": codec,
        "metadata": probe_payload,
    }


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
