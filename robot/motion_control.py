"""Local heading hold and explicit telemetry frame transforms.

Positive SDK z command increases reported yaw (verified hardware pulse log).
These controls never replace SafetyController or its watchdog.
"""
import math


def heading_correction(reference_deg, current_deg):
    if not all(type(v) in (int, float) and math.isfinite(v) for v in (reference_deg, current_deg)):
        raise ValueError("invalid_heading")
    error = (reference_deg - current_deg + 180) % 360 - 180
    if abs(error) > 5:
        raise ValueError("heading_deviation_exceeded_5deg")
    if abs(error) <= 0.3:
        return 0.0
    return max(-3.0, min(3.0, error * 1.5))


def project_initial_body(delta_x, delta_y, initial_yaw_deg):
    if not all(type(v) in (int, float) and math.isfinite(v) for v in (delta_x, delta_y, initial_yaw_deg)):
        raise ValueError("invalid_pose_delta")
    angle = math.radians(initial_yaw_deg)
    return (delta_x * math.cos(angle) + delta_y * math.sin(angle),
            -delta_x * math.sin(angle) + delta_y * math.cos(angle))
