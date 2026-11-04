"""Offline environment check: never creates a Robot or connects to hardware."""

import importlib.metadata
import json
import platform
import sys
from fractions import Fraction


def check():
    if sys.version_info[:2] != (3, 11):
        raise RuntimeError("Use the pinned Python 3.11 robot/.venv environment")
    import av
    import cv2
    import netifaces
    import numpy as np
    import libmedia_codec
    from robomaster import robot, camera, robotic_arm, gripper, sensor

    sdk_dist = importlib.metadata.distribution("robomaster")
    provenance = json.loads(sdk_dist.read_text("direct_url.json") or "{}")
    source_commit = provenance.get("vcs_info", {}).get("commit_id")
    if source_commit != "ff6646e115ab125af3207a4ed3df42cc76c795b2":
        raise RuntimeError("SDK provenance differs from the pinned official Git commit; reinstall from requirements-macos.lock with --force-reinstall")

    # Actual H.264 encode/decode round trip, including fragmented stream input.
    encoder = av.CodecContext.create("libx264", "w")
    encoder.width, encoder.height = 64, 48
    encoder.pix_fmt = "yuv420p"
    encoder.time_base = Fraction(1, 25)
    encoder.options = {"preset": "ultrafast", "tune": "zerolatency"}
    source = np.zeros((48, 64, 3), dtype=np.uint8)
    source[:, :, 2] = 200
    stream = b""
    for i in range(4):
        frame = av.VideoFrame.from_ndarray(source, format="bgr24")
        frame.pts = i
        stream += b"".join(bytes(p) for p in encoder.encode(frame))
    stream += b"".join(bytes(p) for p in encoder.encode(None))
    decoder = libmedia_codec.H264Decoder()
    decoded = []
    for start in range(0, len(stream), 37):
        decoded.extend(decoder.decode(stream[start:start + 37]))
    if not decoded:
        raise RuntimeError("H.264 decoder produced no frames")
    pixels, width, height, stride = decoded[0]
    image = np.frombuffer(pixels, dtype=np.uint8).reshape(height, width, 3)
    if (width, height, stride) != (64, 48, 192):
        raise RuntimeError("Unexpected decoded dimensions/stride")
    if not (image[:, :, 2].mean() > 180 and image[:, :, 0].mean() < 20):
        raise RuntimeError("Decoded color order or content is incorrect")
    ok, jpeg = cv2.imencode(".jpg", image)
    if not ok or cv2.imdecode(jpeg, cv2.IMREAD_COLOR) is None:
        raise RuntimeError("OpenCV JPEG round trip failed")
    # Exercise real Opus decoding, not just class import/creation.
    audio_encoder = av.CodecContext.create("libopus", "w")
    audio_encoder.sample_rate = 48000
    audio_encoder.layout = "mono"
    audio_encoder.format = "s16"
    audio_encoder.time_base = Fraction(1, 48000)
    audio = av.AudioFrame.from_ndarray(np.zeros((1, 960), dtype=np.int16),
                                       format="s16", layout="mono")
    audio.sample_rate = 48000
    packets = list(audio_encoder.encode(audio)) + list(audio_encoder.encode(None))
    opus = libmedia_codec.OpusDecoder()
    if not any(opus.decode(bytes(packet)) for packet in packets):
        raise RuntimeError("Opus decoder produced no PCM samples")
    return {
        "python": platform.python_version(), "architecture": platform.machine(),
        "sdk": importlib.metadata.version("robomaster"),
        "sdk_source_commit": source_commit,
        "codec": "local PyAV compatibility adapter",
        "sdk_module_imports": "PASS: robot/camera/arm/gripper/sensor",
        "h264_fragmented_decode_and_color": "PASS",
        "jpeg_round_trip": "PASS", "opus_decode": "PASS",
        "network_interfaces_visible": bool(netifaces.interfaces()),
        "hardware_connection": "NOT ATTEMPTED",
        "hardware_capabilities": "USER REPORTED, NOT QUERIED",
    }


if __name__ == "__main__":
    try:
        print(json.dumps(check(), indent=2))
    except Exception as exc:
        print(f"Environment check FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
