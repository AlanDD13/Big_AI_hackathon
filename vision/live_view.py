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

from vision.graspability import estimate_graspability, limits_from_env, scale_from_reference


def annotation_lines(obj, mm_per_px, max_opening):
    """Keep semantic identification, detector scores and physical evidence separate."""
    detector = f"YOLO: {obj['detectorLabel']} {obj['detectorScore']:.2f} | {obj['motionState']}"
    classification = obj.get('classification')
    exclusion = obj.get('exclusionReason')
    if classification and (classification.get('subject_kind') in {'PERSON', 'ANIMAL'}
                           or classification.get('carrying') == 'YES'):
        exclusion = 'LIVING_OR_CARRIED'
    if exclusion:
        return [detector, f"Pickup: EXCLUDED ({exclusion})"]
    if obj.get('classificationStatus') == 'COMPLETED' and classification:
        fixture = ' [FIXTURE]' if obj.get('classificationProvider') == 'fixture' else ''
        semantic = f"Grok{fixture}: {classification['description'][:75]} ({classification['confidence']})"
    else:
        semantic = f"Grok: {obj.get('classificationStatus', 'PENDING')}"
    size = estimate_graspability(obj['bboxPx'], mm_per_px, max_opening)
    if size.verdict == 'UNKNOWN_SCALE':
        pickup = 'Pickup: UNKNOWN - no measured size scale (press c)'
    elif size.verdict in {'TOO_LARGE', 'TOO_SMALL'}:
        pickup = f'Pickup: SIZE REJECTED ({size.verdict}) - advisory'
    else:
        pickup = 'Pickup: UNVERIFIED - size fits; grip/thickness/weight untested'
    visual = obj.get('visualPickupAdvisory')
    lines = [semantic, detector]
    if visual:
        lines.append(f"Grok EP Core estimate: {visual.get('robot_grasp_estimate', 'UNKNOWN')} "
                     f"({visual.get('estimate_confidence', 'LOW')}) | {visual['object_form']}")
    return [*lines, pickup]


def frame_reference(camera, objects):
    """Show the last processed image even when it contains zero detections."""
    stream = camera.get('stream') or {}
    processed = camera.get('processed') or {}
    if stream and processed.get('sessionId') == stream.get('session_id'):
        return processed['sessionId'], processed['frameId'], processed['geometryVersion']
    if stream:
        return stream['session_id'], stream['latest_frame_id'], None
    current = [o for o in objects if o.get('status') != 'LOST']
    if current:
        obj = max(current, key=lambda o: o['lastSeenAt'])
        return obj['sessionId'], obj['frameId'], obj.get('geometryVersion')
    return None


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
    scale_epoch = None
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
            cameras = json.loads(_get(f"{base}/api/cameras", token, context))["cameras"]
            camera = next((c for c in cameras if c['cameraId'] == args.camera), {})
            reference = frame_reference(camera, mine)
            if reference:
                session_id, frame_id, geometry_version = reference
                path = f"{base}/api/frames/{args.camera}/{session_id}/{frame_id}"
                frame = cv2.imdecode(np.frombuffer(_get(path, token, context), np.uint8), cv2.IMREAD_COLOR)
                if frame is not None:
                    epoch = session_id, geometry_version, frame.shape[:2]
                    if scale_epoch is not None and epoch != scale_epoch:
                        mm_per_px = None
                    scale_epoch = epoch
                    for o in mine:
                        if o['sessionId'] != session_id or o["frameId"] != frame_id or o.get('status') == 'LOST':
                            continue
                        x1, y1, x2, y2 = (int(v) for v in o["bboxPx"])
                        excluded = bool(o.get("exclusionReason"))
                        color = (0, 0, 255) if excluded else (0, 200, 0)
                        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                        for line, tag in enumerate(annotation_lines(o, mm_per_px, max_opening)):
                            y = min(frame.shape[0] - 6, max(18, y1 - 6) + line * 18)
                            cv2.putText(frame, tag, (max(0, min(x1, frame.shape[1] - 300)), y),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)
                    cv2.putText(frame, 'c: measure reference width | q: quit | Pickup estimates are advisory',
                                (8, frame.shape[0] - 10), cv2.FONT_HERSHEY_SIMPLEX, .45, (0, 180, 255), 1)
                    cv2.imshow(f"vision {args.camera}", frame)
        except Exception as exc:  # keep polling through transient errors
            print("poll error:", exc, file=sys.stderr)
        key = cv2.waitKey(200) & 0xFF
        if key == ord("q"):
            break
        if key == ord('c') and 'frame' in locals() and frame is not None:
            # The operator must place a measured reference on the same floor
            # plane/depth as the object; this is not mapping calibration.
            _, _, pixels, _ = cv2.selectROI('Select reference WIDTH, Enter to accept', frame, False)
            cv2.destroyWindow('Select reference WIDTH, Enter to accept')
            if pixels:
                try:
                    real_mm = float(input('Actual measured reference WIDTH in millimetres: '))
                    mm_per_px = scale_from_reference(real_mm, pixels)
                    print('Local size scale set. Grip, thickness and weight still require physical verification.')
                except (ValueError, EOFError):
                    mm_per_px = None
                    print('Invalid reference; pickup remains UNKNOWN.')
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
