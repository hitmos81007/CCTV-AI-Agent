import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np
from contextlib import closing

with tempfile.TemporaryDirectory(prefix="cctv-recovery-") as folder:
    root = Path(folder)
    os.environ["APP_DATA_DIR"] = folder
    os.environ["CCTV_JOB_LEASE_SECONDS"] = "5"
    os.environ["YOLO_DEVICE"] = "cpu"
    os.environ["YOLO_MODEL"] = str(Path("yolo11n.pt").resolve())
    os.environ["YOLO_CONFIG_DIR"] = str(root / "ultralytics")
    os.environ["DATABASE_URL"] = ""
    from app import db

    db.initialize()
    clip = root / "synthetic-bus.mp4"
    writer = cv2.VideoWriter(str(clip), cv2.VideoWriter_fourcc(*"mp4v"), 5, (320, 240))
    assert writer.isOpened()
    sample = Path(r"C:\Users\mohit\Documents\Codex\cctv-video-env\Lib\site-packages\ultralytics\assets\bus.jpg")
    image = cv2.imread(str(sample))
    assert image is not None
    image = cv2.resize(image, (320, 240))
    for _ in range(40):
        writer.write(image)
    writer.release()
    import hashlib
    digest = hashlib.sha256(clip.read_bytes()).hexdigest()
    clue = {"visual_targets": [{"target_type": "bus", "color": None, "attributes": []}],
            "source_text": "bus", "parser": "test_fixture"}
    request = {"case_id": "RECOVERY", "worker_id": "worker-Z1", "jurisdiction": "Z1",
               "video_source_id": "video", "clue": clue}
    with db.connection() as conn:
        conn.execute("INSERT INTO cases(case_id,description,initial_zone,neighbors_json,clues_json,created_at) VALUES(?,?,?,?,?,?)",
                     ("RECOVERY", "Fictional recovery case", "Z1", '{"Z1":[]}', json.dumps(clue), db.utc_now()))
        conn.execute("INSERT INTO videos(video_source_id,case_id,jurisdiction,camera_id,original_name,file_path,recorded_at,sha256,byte_size,duration_seconds,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                     ("video", "RECOVERY", "Z1", "CAM", clip.name, str(clip), "2026-11-26T10:00:00+00:00", digest, clip.stat().st_size, 8, db.utc_now()))
        conn.execute("INSERT INTO investigation_jobs(id,case_id,jurisdiction,video_source_id,worker_id,status,created_at,updated_at,request_json) VALUES(?,?,?,?,?,?,?,?,?)",
                     ("job", "RECOVERY", "Z1", "video", "worker-Z1", "queued", db.utc_now(), db.utc_now(), json.dumps(request)))

    def job():
        with closing(sqlite3.connect(db.DB_PATH)) as conn:
            return conn.execute("SELECT status,attempts FROM investigation_jobs WHERE id='job'").fetchone()

    first = subprocess.Popen([sys.executable, "-m", "app.worker", "--once"], env=os.environ.copy(),
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline and job()[0] != "running":
            if first.poll() is not None:
                raise RuntimeError("First worker exited before claiming job: " + first.stderr.read().decode(errors="replace"))
            time.sleep(0.05)
        assert job()[0] == "running"
    finally:
        first.terminate()
        first.wait(timeout=10)
    time.sleep(6)
    second = subprocess.run([sys.executable, "-m", "app.worker", "--once"], env=os.environ.copy(),
                            capture_output=True, text=True, timeout=180, creationflags=subprocess.CREATE_NO_WINDOW)
    assert second.returncode == 0, second.stderr
    assert job() == ("completed", 2), (job(), second.stderr)
    with closing(sqlite3.connect(db.DB_PATH)) as conn:
        sightings = conn.execute("SELECT count(*) FROM sightings").fetchone()[0]
        distinct_tracks = conn.execute("SELECT count(DISTINCT track_id) FROM sightings").fetchone()[0]
    assert sightings > 0 and sightings == distinct_tracks, (sightings, distinct_tracks)
    third = subprocess.run([sys.executable, "-m", "app.worker", "--once"], env=os.environ.copy(),
                           capture_output=True, text=True, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW)
    assert third.returncode == 0, third.stderr
    with closing(sqlite3.connect(db.DB_PATH)) as conn:
        after = conn.execute("SELECT count(*) FROM sightings").fetchone()[0]
    assert after == sightings
    print(f"PASS: interrupted worker recovered on attempt two with {sightings} actual synthetic-clip track(s); another worker run created no duplicate sightings")
