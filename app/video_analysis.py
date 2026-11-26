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
        crop = frame[y1 + (y2 - y1) * 2 // 5 : y1 + (y2 - y1) * 4 // 5, x1:x2]
    else:
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
                x1, y1, x2, y2 = [int(value) for value in xyxys[index]]
                evidence_frame = frame.copy()
                cv2.rectangle(evidence_frame, (x1, y1), (x2, y2), (40, 220, 120), max(2, evidence_frame.shape[1] // 900))
                label = f"{target_type} {confidence_value:.2f} | {frame_time.isoformat(timespec='seconds')}"
                cv2.putText(evidence_frame, label, (max(8, x1), max(24, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX,
                            max(0.55, evidence_frame.shape[1] / 1800), (40, 220, 120), 2, cv2.LINE_AA)
                # Preserve the source frame with a visible detector box, not a video seek link.
                scale = min(1.0, 1280 / max(evidence_frame.shape[:2]))
                if scale < 1.0:
                    evidence_frame = cv2.resize(evidence_frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                encoded, jpeg = cv2.imencode(".jpg", evidence_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
                if encoded:
                    tracks[key]["frame_jpeg"] = jpeg.tobytes()
                    tracks[key]["frame_offset_seconds"] = frame_index / fps
    return list(tracks.values())

