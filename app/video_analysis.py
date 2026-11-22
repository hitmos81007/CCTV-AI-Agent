from __future__ import annotations

import os
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

VEHICLE_CLASSES = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}


@lru_cache(maxsize=1)
def _model() -> Any:
    from ultralytics import YOLO

    return YOLO(os.getenv("YOLO_MODEL", "yolo11n.pt"))


def _vehicle_color(frame, xyxy: list[float]) -> str | None:
    import cv2

    height, width = frame.shape[:2]
    x1, y1, x2, y2 = [int(v) for v in xyxy]
    x1, x2 = max(0, x1), min(width, x2)
    y1, y2 = max(0, y1), min(height, y2)
    if x2 <= x1 or y2 <= y1:
        return None
    crop = frame[y1 + (y2 - y1) // 3 : y2, x1:x2]
    if crop.size == 0:
        return None
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    h, s, v = [float(x) for x in cv2.mean(hsv)[:3]]
    if v < 55:
        return "black"
    if s < 30:
        return "white" if v > 190 else "gray"
    if h < 8 or h >= 170:
        return "red"
    if h < 20:
        return "orange"
    if h < 35:
        return "yellow"
    if h < 85:
        return "green"
    if h < 130:
        return "blue"
    return "purple"


def analyze_video(path: str | Path, recorded_at: datetime, confidence: float = 0.30) -> list[dict[str, Any]]:
    """Track vehicle classes only. No person, face, or plate analysis is performed."""
    import cv2

    path = str(path)
    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        raise ValueError("The uploaded file is not a readable video")
    fps = capture.get(cv2.CAP_PROP_FPS) or 25.0
    capture.release()

    model = _model()
    device = os.getenv("YOLO_DEVICE", "0")
    tracks: dict[str, dict[str, Any]] = {}
    for frame_index, result in enumerate(
        model.track(source=path, stream=True, tracker="bytetrack.yaml", device=device, conf=confidence, verbose=False)
    ):
        frame = result.orig_img
        frame_time = recorded_at + timedelta(seconds=frame_index / fps)
        boxes = result.boxes
        if boxes is None:
            continue
        ids = boxes.id.int().cpu().tolist() if boxes.id is not None else [None] * len(boxes)
        classes = boxes.cls.int().cpu().tolist()
        confidences = boxes.conf.cpu().tolist()
        xyxys = boxes.xyxy.cpu().tolist()
        for index, class_id in enumerate(classes):
            if class_id not in VEHICLE_CLASSES:
                continue
            track_id = ids[index]
            if track_id is None:
                continue
            key = str(track_id)
            confidence_value = float(confidences[index])
            color = _vehicle_color(frame, xyxys[index])
            current = tracks.get(key)
            if current is None:
                tracks[key] = {
                    "track_id": int(track_id),
                    "first_seen_at": frame_time,
                    "last_seen_at": frame_time,
                    "start_offset_seconds": frame_index / fps,
                    "end_offset_seconds": frame_index / fps,
                    "vehicle_type": VEHICLE_CLASSES[class_id],
                    "vehicle_color": color,
                    "detector_confidence": confidence_value,
                }
            else:
                current["last_seen_at"] = frame_time
                current["end_offset_seconds"] = frame_index / fps
                if confidence_value > current["detector_confidence"]:
                    current["detector_confidence"] = confidence_value
                    current["vehicle_color"] = color
                    current["vehicle_type"] = VEHICLE_CLASSES[class_id]
    return list(tracks.values())

