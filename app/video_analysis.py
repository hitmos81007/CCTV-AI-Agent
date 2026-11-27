from __future__ import annotations

import os
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

TARGET_CLASSES = {0: "person", 1: "bicycle", 2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}


def _resolve_device(configured: str | None = None, cuda_available: bool | None = None) -> int | str:
    """Use CUDA when available by default; allow an explicit CPU/GPU override."""
    requested = (configured if configured is not None else os.getenv("YOLO_DEVICE", "auto")).strip()
    if requested.casefold() in {"", "auto"}:
        if cuda_available is None:
            import torch

            cuda_available = bool(torch.cuda.is_available())
        return 0 if cuda_available else "cpu"
    return int(requested) if requested.isdigit() else requested


@lru_cache(maxsize=1)
def _model() -> Any:
    from ultralytics import YOLO

    return YOLO(os.getenv("YOLO_MODEL", "yolo11n.pt"))


def _dominant_color(frame, xyxy: list[float], target_type: str) -> str | None:
    import cv2

    height, width = frame.shape[:2]
    x1, y1, x2, y2 = [int(v) for v in xyxy]
    x1, x2 = max(0, x1), min(width, x2)
    y1, y2 = max(0, y1), min(height, y2)
    if x2 <= x1 or y2 <= y1:
        return None
    if target_type == "person":
        # Approximate clothing color from the torso only; never inspect the head/face.
        crop = frame[y1 + (y2 - y1) * 2 // 5 : y1 + (y2 - y1) * 4 // 5, x1 + (x2 - x1) // 5 : x2 - (x2 - x1) // 5]
    else:
        crop = frame[y1 + (y2 - y1) // 3 : y1 + (y2 - y1) * 3 // 4, x1 + (x2 - x1) // 5 : x2 - (x2 - x1) // 5]
    if crop.size == 0:
        crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return None
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    h, s, v = [float(x) for x in cv2.mean(hsv)[:3]]
    if v < 55:
        return "black"
    if s < 30:
        return "white" if v > 190 else "gray"
    if h < 12 or h >= 165:
        return "red"
    if h < 24:
        return "orange"
    if h < 35:
        return "yellow"
    if h < 85:
        return "green"
    if h < 130:
        return "blue"
    return "purple"


def analyze_video(path: str | Path, recorded_at: datetime | None, confidence: float = 0.30) -> list[dict[str, Any]]:
    """Track visible people and common vehicles/objects within one clip only.

    Track IDs are ephemeral to this one video. No face, license-plate, biometric,
    or cross-camera identity matching is performed.
    """
    import cv2

    path = str(path)
    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        raise ValueError("The uploaded file is not a readable video")
    fps = capture.get(cv2.CAP_PROP_FPS) or 25.0
    capture.release()

    model = _model()
    device = _resolve_device()
    tracks: dict[str, dict[str, Any]] = {}
    for frame_index, result in enumerate(
        model.track(source=path, stream=True, tracker="bytetrack.yaml", persist=False, device=device, conf=confidence, verbose=False)
    ):
        frame = result.orig_img
        offset_sec = frame_index / fps
        frame_time = (recorded_at + timedelta(seconds=offset_sec)) if recorded_at is not None else None
        boxes = result.boxes
        if boxes is None:
            continue
        ids = boxes.id.int().cpu().tolist() if boxes.id is not None else [None] * len(boxes)
        classes = boxes.cls.int().cpu().tolist()
        confidences = boxes.conf.cpu().tolist()
        xyxys = boxes.xyxy.cpu().tolist()
        for index, class_id in enumerate(classes):
            if class_id not in TARGET_CLASSES:
                continue
            track_id = ids[index]
            if track_id is None:
                continue
            key = str(track_id)
            confidence_value = float(confidences[index])
            target_type = TARGET_CLASSES[class_id]
            color = _dominant_color(frame, xyxys[index], target_type)
            current = tracks.get(key)
            if current is None:
                tracks[key] = {
                    "track_id": int(track_id),
                    "first_seen_at": frame_time,
                    "last_seen_at": frame_time,
                    "start_offset_seconds": frame_index / fps,
                    "end_offset_seconds": frame_index / fps,
                    "subject_type": target_type,
                    "vehicle_type": target_type,
                    "vehicle_color": color,
                    "visual_attributes": ([f"approximate torso color: {color}"] if target_type == "person" and color else []),
                    "detector_confidence": confidence_value,
                }
            else:
                current["last_seen_at"] = frame_time
                current["end_offset_seconds"] = frame_index / fps
                if confidence_value > current["detector_confidence"]:
                    current["detector_confidence"] = confidence_value
                    current["vehicle_color"] = color
                    current["subject_type"] = target_type
                    current["vehicle_type"] = target_type
                    current["visual_attributes"] = ([f"approximate torso color: {color}"] if target_type == "person" and color else [])
            if confidence_value >= tracks[key]["detector_confidence"]:
                orig_h, orig_w = frame.shape[:2]
                x1, y1, x2, y2 = [int(value) for value in xyxys[index]]
                scale = min(1.0, 1280 / max(orig_h, orig_w))
                if scale < 1.0:
                    clean_frame = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                    scaled_bbox = [int(round(x1 * scale)), int(round(y1 * scale)), int(round(x2 * scale)), int(round(y2 * scale))]
                else:
                    clean_frame = frame.copy()
                    scaled_bbox = [x1, y1, x2, y2]
                # Preserve the clean untouched raw source frame without burnt annotations for forensic integrity
                encoded, jpeg = cv2.imencode(".jpg", clean_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
                if encoded:
                    tracks[key]["frame_jpeg"] = jpeg.tobytes()
                    tracks[key]["frame_offset_seconds"] = frame_index / fps
                    tracks[key]["bbox"] = [x1, y1, x2, y2]
                    tracks[key]["scaled_bbox"] = scaled_bbox
                    tracks[key]["frame_width"] = orig_w
                    tracks[key]["frame_height"] = orig_h
                    tracks[key]["saved_frame_width"] = clean_frame.shape[1]
                    tracks[key]["saved_frame_height"] = clean_frame.shape[0]
    return list(tracks.values())

