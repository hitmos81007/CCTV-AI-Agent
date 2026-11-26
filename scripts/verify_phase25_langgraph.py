import os
import tempfile
from pathlib import Path

import cv2
import numpy as np
from fastapi.testclient import TestClient

with tempfile.TemporaryDirectory(prefix="cctv-graph-check-") as folder:
    os.environ["APP_DATA_DIR"] = folder
    os.environ["CORS_ALLOWED_ORIGINS"] = ""
    os.environ["REVIEW_USERNAME"] = "test"
    os.environ["REVIEW_PASSWORD"] = "test"
    os.environ["DATABASE_URL"] = os.getenv("PHASE25_TEST_DATABASE_URL", "")
    from app.main import app
    from app import db

    clip = Path(folder) / "fixture.mp4"
    writer = cv2.VideoWriter(str(clip), cv2.VideoWriter_fourcc(*"mp4v"), 5, (320, 240))
    assert writer.isOpened()
    for _ in range(5):
        writer.write(np.zeros((240, 320, 3), dtype=np.uint8))
    writer.release()
    zones = {f"Zone-{i}": [f"Zone-{(i % 6) + 1}"] for i in range(1, 7)}
    with TestClient(app) as client:
        client.auth = ("test", "test")
        result = client.post("/api/v1/cases", json={"case_id": "GRAPH-TEST",
            "description": "A blue car was stolen near Zone-1", "initial_zone": "Zone-1", "neighbors": zones})
        assert result.status_code == 201, result.text
        video_ids = {}
        for index, zone in enumerate(zones, 1):
            camera = client.post("/api/v1/cameras", json={"camera_id": f"CAM-{index}", "name": f"Camera {index}",
                "jurisdiction": zone, "source_type": "upload"})
            assert camera.status_code == 201, camera.text
            with clip.open("rb") as source:
                result = client.post("/api/v1/videos", data={"case_id": "GRAPH-TEST",
                    "camera_id": f"CAM-{index}", "jurisdiction": zone, "recorded_at": "2026-11-26T10:00:00+00:00"},
                    files={"file": ("fixture.mp4", source, "video/mp4")})
            assert result.status_code == 201, result.text
            video_ids[zone] = result.json()["video_source_id"]
        # A database fixture exercises the review gate; it is not a detector result.
        with db.connection() as conn:
            conn.execute("INSERT INTO sightings(id,case_id,worker_id,jurisdiction,camera_id,video_source_id,occurred_at,video_offset_seconds,vehicle_type,detector_confidence,evidence_sha256,review_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         ("TEST-FIXTURE", "GRAPH-TEST", "fixture", "Zone-1", "CAM-1", video_ids["Zone-1"],
                          "2026-11-26T10:00:00+00:00", 0, "car", 0.0, "fixture-only", "confirmed_candidate", db.utc_now()))
        result = client.post("/api/v1/cases/GRAPH-TEST/plan", headers={"Idempotency-Key": "fixture-search-1"}, timeout=90)
        assert result.status_code == 200, result.text
        planned = result.json()
        assert len(planned["jobs"]) == 6
        assert {job["jurisdiction"] for job in planned["jobs"]} == set(zones)
        assert [(f["source_sighting_id"], f["target_zone"]) for f in planned["followups"]] == [("TEST-FIXTURE", "Zone-2")]
        repeated = client.post("/api/v1/cases/GRAPH-TEST/plan", headers={"Idempotency-Key": "fixture-search-1"})
        assert repeated.status_code == 200 and repeated.json()["run_id"] == planned["run_id"]
        progress = client.get("/api/v1/cases/GRAPH-TEST/progress").json()
        assert len([job for job in progress["jobs"] if job["status"] == "queued"]) == 6
        registry = client.get("/api/v1/recordings?case_id=GRAPH-TEST").json()
        assert len(registry["items"]) == 6
        print("PASS: six zones, cameras, clips, live Ollama LangGraph, six queued jobs, and reviewed fixture follow-up")
