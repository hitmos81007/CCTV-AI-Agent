import os
import subprocess
import tempfile
import time
from pathlib import Path

import imageio_ffmpeg
from fastapi.testclient import TestClient

original = Path(r"C:\Users\mohit\Downloads\12566041-uhd_3840_2160_30fps.mp4")
with tempfile.TemporaryDirectory(prefix="cctv-ingest-") as folder:
    os.environ["APP_DATA_DIR"] = folder
    os.environ["CORS_ALLOWED_ORIGINS"] = ""
    os.environ["REVIEW_USERNAME"] = "test"
    os.environ["REVIEW_PASSWORD"] = "test"
    os.environ["CCTV_IMPORT_ROOTS"] = str(original.parent)
    os.environ["CCTV_ALLOWED_RTSP_HOSTS"] = "127.0.0.1"
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
    os.environ["FFPROBE_PATH"] = str(Path("work/phase25-video-tools/node_modules/ffprobe-static/bin/win32/x64/ffprobe.exe").resolve())
    os.environ["DATABASE_URL"] = os.getenv("PHASE25_TEST_DATABASE_URL", "")
    publisher = subprocess.Popen([
        imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-stream_loop", "-1",
        "-re", "-i", str(original), "-vf", "scale=640:-2", "-an", "-c:v", "libx264",
        "-preset", "ultrafast", "-f", "rtsp", "-rtsp_transport", "tcp", "rtsp://127.0.0.1:18554/test"
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        from app.main import app
        time.sleep(3)
        assert publisher.poll() is None, "RTSP publisher exited before test"
        zones = {f"Z{i}": [] for i in range(1, 7)}
        with TestClient(app) as client:
            client.auth = ("test", "test")
            result = client.post("/api/v1/cases", json={"case_id": "INGEST-TEST",
                "description": "Fictional vehicle test", "initial_zone": "Z1", "neighbors": zones})
            assert result.status_code == 201, result.text
            for camera, kind, zone in [("DVR", "dvr", "Z1"), ("CAM", "ip_camera", "Z2")]:
                result = client.post("/api/v1/cameras", json={"camera_id": camera, "name": camera,
                    "jurisdiction": zone, "source_type": kind})
                assert result.status_code == 201, result.text
            ids = []
            for camera, kind, uri in [("DVR", "dvr", original.as_uri()),
                                      ("CAM", "ip_camera", "rtsp://127.0.0.1:18554/test")]:
                result = client.post("/api/v1/recordings", json={"case_id": "INGEST-TEST",
                    "camera_id": camera, "source_type": kind, "source_uri": uri,
                    "recorded_at": "2026-11-26T10:00:00+00:00"})
                assert result.status_code == 201, result.text
                ids.append(result.json()["id"])
            for rid in ids:
                result = client.post(f"/api/v1/recordings/{rid}/ingest?seconds=2")
                assert result.status_code == 200, result.text
                assert client.get(f"/api/v1/recordings/{rid}").json()["status"] == "ready"
                assert client.get(f"/api/v1/recordings/{rid}").json()["metadata"]["probe"] == "ffprobe"
            with original.open("rb") as source:
                result = client.post("/api/v1/videos", data={"case_id": "INGEST-TEST", "camera_id": "UPLOAD",
                    "jurisdiction": "Z3", "recorded_at": "2026-11-26T10:00:00+00:00"},
                    files={"file": ("sample.mp4", source, "video/mp4")})
            assert result.status_code == 201, result.text
            items = client.get("/api/v1/recordings?case_id=INGEST-TEST").json()["items"]
            assert {x["source_type"] for x in items} == {"dvr", "ip_camera", "upload"}, items
            assert all(x["video_source_id"] for x in items)
            assert len(items) == 3, len(items)
            print("PASS: local RTSP capture, DVR import, FFprobe, uploaded file and one shared recording list")
    finally:
        publisher.terminate()
        publisher.wait(timeout=10)
