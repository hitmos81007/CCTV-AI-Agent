"""ONVIF Profile G adapter for NVR recording discovery and replay URLs."""
from __future__ import annotations

import os
from typing import Protocol
from urllib.parse import urlsplit


class NVRAdapter(Protocol):
    def recordings(self): ...
    def replay_uri(self, token: str) -> str: ...


class OnvifNVR:
    def __init__(self, camera: dict):
        from onvif import ONVIFCamera

        parsed = urlsplit(camera["source_uri"] or "")
        allowed = {item.strip().lower() for item in os.getenv("CCTV_ALLOWED_RTSP_HOSTS", "").split(",") if item.strip()}
        if not parsed.hostname or parsed.hostname.lower() not in allowed:
            raise PermissionError("NVR host is not in CCTV_ALLOWED_RTSP_HOSTS")
        key = "".join(ch if ch.isalnum() else "_" for ch in camera["camera_id"]).upper()
        user = os.getenv("CCTV_RTSP_USER_" + key)
        password = os.getenv("CCTV_RTSP_PASSWORD_" + key)
        if not user or not password:
            raise ValueError("NVR credentials are not configured")
        port = int(os.getenv("CCTV_ONVIF_PORT_" + key, "80"))
        if not 1 <= port <= 65535:
            raise ValueError("Invalid ONVIF port")
        self.device = ONVIFCamera(parsed.hostname, port, user, password)
        self.allowed = allowed

    def recordings(self):
        from zeep.helpers import serialize_object
        return serialize_object(self.device.create_recording_service().GetRecordings(), dict)

    def replay_uri(self, token: str) -> str:
        replay = self.device.create_replay_service()
        request = replay.create_type("GetReplayUri")
        request.RecordingToken = token
        request.StreamSetup = {"Stream": "RTP-Unicast", "Transport": {"Protocol": "RTSP"}}
        response = replay.GetReplayUri(request)
        uri = str(getattr(response, "Uri", response))
        parsed = urlsplit(uri)
        if parsed.scheme not in ("rtsp", "rtsps") or not parsed.hostname or parsed.hostname.lower() not in self.allowed:
            raise ValueError("NVR returned a replay URI outside the authorized host list")
        if parsed.username or parsed.password:
            raise ValueError("NVR returned a URI containing credentials")
        return uri
