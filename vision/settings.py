"""Configuration is operator supplied, never taken from incoming image content."""
from dataclasses import dataclass, field
import os
import json
import re


@dataclass
class Settings:
    token: str
    cameras: dict[str, str] = field(default_factory=lambda: {"phone-1": "FIXED", "phone-2": "FIXED", "mac-1": "FIXED", "rover-1": "ROVER"})
    mapping_url: str | None = None
    backend_url: str | None = None
    peer_token: str | None = None
    stale_seconds: float = 1.0
    stationary_seconds: float = 5.0
    max_movement_m: float = 0.05
    max_position_error_m: float = 0.025
    model: str = "yoloe-26s-seg-pf.pt"
    device: str = "mps"
    max_objects: int = 500
    max_pending_classifications: int = 16
    local_sources: dict = field(default_factory=dict)
    rover_bridge_url: str | None = None
    rover_camera_id: str = "rover-1"
    clock: object | None = None

    @classmethod
    def from_env(cls):
        token = os.getenv("VISION_TOKEN", "")
        if not token or len(token) < 16:
            raise ValueError("Set VISION_TOKEN to an unpredictable value of at least 16 characters")
        camera_text = os.getenv("VISION_CAMERAS", "phone-1:FIXED,phone-2:FIXED,mac-1:FIXED,rover-1:ROVER")
        cameras = {}
        for item in camera_text.split(","):
            camera_id, kind = item.strip().rsplit(":", 1)
            if kind not in {"FIXED", "ROVER"} or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", camera_id) or camera_id in cameras:
                raise ValueError("VISION_CAMERAS must contain unique id:FIXED or id:ROVER entries")
            cameras[camera_id] = kind
        sources = json.loads(os.getenv("VISION_LOCAL_SOURCES", "{}"))
        if not isinstance(sources, dict) or any(key not in cameras or cameras[key] != "FIXED"
                or type(value) not in (str, int) for key, value in sources.items()):
            raise ValueError("VISION_LOCAL_SOURCES must map configured fixed cameras to device indexes or video files")
        rover_id = os.getenv("ROVER_CAMERA_ID", "rover-1")
        if os.getenv("ROVER_BRIDGE_URL") and cameras.get(rover_id) != "ROVER":
            raise ValueError("ROVER_CAMERA_ID must identify a configured ROVER camera")
        return cls(token=token, cameras=cameras, local_sources=sources,
                   rover_bridge_url=os.getenv("ROVER_BRIDGE_URL") or None, rover_camera_id=rover_id,
                   mapping_url=os.getenv("MAPPING_URL") or None,
                   backend_url=os.getenv("BACKEND_URL") or None,
                   peer_token=os.getenv("PEER_TOKEN") or None,
                   model=os.getenv("VISION_MODEL", "yoloe-26s-seg-pf.pt"),
                   device=os.getenv("VISION_DEVICE", "mps"))
