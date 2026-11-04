"""Debug viewer: draws vision-service detections on the latest camera frame.

Run from the repo root (token from vision/.env):
  set -a; source vision/.env; set +a
  vision/.venv/bin/python -m vision.live_view --url https://192.168.5.192:8443 --camera phone-1

Press q in the window to quit. Diagnostic only; boxes are image-space detections, not
permission to drive or pick up. Certificate checks are skipped for the LAN self-signed cert.
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.request

import cv2
import numpy as np

from vision.graspability import estimate_graspability, limits_from_env


def _get(url, token, context):
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=3, context=context) as response:
        return response.read()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://127.0.0.1:8443")
    parser.add_argument("--camera", default="phone-1")
    parser.add_argument("--mm-per-px", type=float, default=float(os.getenv("VISION_MM_PER_PX", "0") or 0) or None,
                        help="Floor scale at the object's depth; enables the size/graspability estimate")
    args = parser.parse_args()
    mm_per_px = args.mm_per_px
    max_opening = limits_from_env()[0]
    token = os.getenv("VISION_TOKEN", "")
    if not token:
        sys.exit("Set VISION_TOKEN (source vision/.env)")
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    base = args.url.rstrip("/")
    while True:
        try:
            objects = json.loads(_get(f"{base}/api/objects", token, context))["objects"]
            mine = [o for o in objects if o["cameraId"] == args.camera]
            if mine:
                latest = max(mine, key=lambda o: o["frameId"])
                path = f"{base}/api/frames/{args.camera}/{latest['sessionId']}/{latest['frameId']}"
                frame = cv2.imdecode(np.frombuffer(_get(path, token, context), np.uint8), cv2.IMREAD_COLOR)
                if frame is not None:
                    for o in mine:
                        if o["frameId"] != latest["frameId"]:
                            continue
                        x1, y1, x2, y2 = (int(v) for v in o["bboxPx"])
                        excluded = bool(o.get("exclusionReason"))
                        color = (0, 0, 255) if excluded else (0, 200, 0)
                        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                        tag = f"{o['detectorLabel']} {o['detectorScore']:.2f} {o['motionState']}"
                        if excluded:
                            tag += f" [{o['exclusionReason']}]"
                        else:
                            grasp = estimate_graspability(o["bboxPx"], mm_per_px, max_opening)
                            tag += f" | {grasp.verdict}"
                            if grasp.width_mm is not None:
                                tag += f" {grasp.width_mm:.0f}x{grasp.height_mm:.0f}mm"
                            if grasp.verdict == "TOO_LARGE":
                                color = (0, 140, 255)
                        cv2.putText(frame, tag, (x1, max(15, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
                    cv2.imshow(f"vision {args.camera}", frame)
        except Exception as exc:  # keep polling through transient errors
            print("poll error:", exc, file=sys.stderr)
        if cv2.waitKey(200) & 0xFF == ord("q"):
            break
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
