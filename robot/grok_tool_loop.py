"""Grok 4.7 drives the rover through rover_tools.TOOLS: dynamic closed-loop tool calling.

Flow:
1. Stream live video feed at http://localhost:8088 (MJPEG 30 FPS).
2. For each step:
   a. Capture latest photo from robot camera
   b. Mirror photo to brain directory for live chat review
   c. Pass photo + status + past actions to Grok 4.7 (1500 tokens budget)
   d. Grok reasons through 4 phases using few-shot guidance
   e. Execute tool call on robot via rover_tools.dispatch
   f. Record photo, Grok reasoning, action, and physical outcome
   g. Repeat until stop_robot
3. Generate visual summary.html and report.json for in-depth post-run analysis.
"""
import base64
import json
import os
import ssl
import sys
import time
from pathlib import Path
from urllib.request import HTTPSHandler, HTTPRedirectHandler, Request, build_opener

import rover_tools as rt

MODEL_KEY = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("ROVER_MODEL", "grok")
MODEL = "x-ai/grok-4.7" if "grok" in MODEL_KEY.lower() else "google/gemini-3.8-flash"
MAX_STEPS = int(sys.argv[1]) if len(sys.argv) > 1 else 20
LOG = Path("/tmp/pavia-robot/logs") / f"tools-{time.time_ns()}"
BRAIN_DIR = Path("/Users/alandd/.gemini/antigravity-cli/brain/dd744dcc-2fe5-4c81-8537-0ceabfe0ca93")

SYSTEM = (
    "You are the autonomous pilot of a DJI RoboMaster EP Core rover equipped with omnidirectional mecanum wheels, "
    "a forward-facing camera, and a 2-DOF robotic arm with gripper jaws.\n"
    "Your objective: locate the metallic drink can standing upright on the open floor, approach it quickly, center it squarely between the gripper jaws, "
    "grasp the can firmly with grasp_can(), lift it, transport it forward, place it back down, and finish.\n"
    "\n"
    "REAL-TIME YOLO DETECTOR (canTracker in robotStatus):\n"
    "The camera is processed in real time (30ms) by an onboard sub-pixel YOLO neural network (`canTracker` in `robotStatus`).\n"
    "When `canTracker['detected']` is True:\n"
    "- `canTracker['recommendedTurnDeg']`: EXACT degrees to turn to center the can. If not 0, call turn_degrees(degrees=canTracker['recommendedTurnDeg'])!\n"
    "- `canTracker['isInCorridor']`: If True (x between 290 and 350), CAN IS CENTERED! DO NOT TURN! DRIVE FORWARD!\n"
    "- `canTracker['isInGraspRange']`: If True (bottomYPx >= 230), CALL grasp_can(0.9) IMMEDIATELY!\n"
    "- `canTracker['distanceToClawsPx']`: Distance remaining to claws.\n"
    "\n"
    "CRITICAL RULES FOR SPEED AND ACCURACY (NO MICRO-TURNING!):\n"
    "The gripper claws open 12 cm wide! That is a huge funnel.\n"
    "1. TURNING: If canTracker gives recommendedTurnDeg != 0 (e.g. +16° or -20°), turn by that exact angle ONCE.\n"
    "   NEVER call turn_degrees with angles < 10°! If isInCorridor is True, IT IS ALIGNED. DRIVE FORWARD!\n"
    "2. FAST APPROACH: When can is far (bottom y < 210 or distanceToClawsPx > 125):\n"
    "   Advance boldly: drive_chassis(x_mps=0.12, duration_s=2.2) (~26 cm advance per step!). Do not creep by 10 cm!\n"
    "3. MID-RANGE APPROACH: When can is approaching (bottom y between 210 and 265):\n"
    "   Advance close to grasp zone: drive_chassis(x_mps=0.08, duration_s=1.0) (~8 cm advance). DO NOT call grasp_can yet while still at mid-range!\n"
    "4. GUARANTEED GRASP: As soon as can bottom reaches y >= 270 (yellow grasp line) or isInGraspRange is True:\n"
    "   CALL grasp_can(forward_duration_s=1.3) IMMEDIATELY!\n"
    "   DO NOT DRIVE FORWARD SEPARATELY WITH drive_chassis (driving forward with closed claws kicks the can!).\n"
    "   grasp_can automatically lowers arm, opens 12cm claws wide, glides forward ~6 cm into the throat, clamps tight at power 80, and lifts can off floor!\n"
    "5. TRANSPORT & FINISH:\n"
    "   Once can is held and lifted (canTracker['canHeldInClaws'] or last action grasp_can):\n"
    "   - Step A: drive_chassis(x_mps=0.08, duration_s=1.2)\n"
    "   - Step B: move_arm(dx_mm=0, dy_mm=-35)\n"
    "   - Step C: control_gripper(state='open', power=50)\n"
    "   - Step D: stop_robot(reason='task completed successfully')!\n"
    "\n"
    "RULES:\n"
    "- Output exactly ONE tool call per step.\n"
    "- Trust your eyes and canTracker to locate the metallic drink can and ignore shadows and floor debris.\n"
    "- When can is in the corridor, DO NOT TURN! Advance directly!\n"
    "- When can bottom reaches y >= 270 (or isInGraspRange is True), call grasp_can(1.3) immediately!\n"
    "- Once released on floor, call stop_robot() immediately!"
)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError("redirect_rejected")


def load_key():
    for path in (Path("/Users/alandd/Desktop/Pavia_grok_hackathon/vision/.env"),
                 Path("/Users/alandd/Desktop/Pavia_grok_hackathon/.env")):
        if path.is_file():
            for line in path.read_text().splitlines():
                if line.startswith("OPENROUTER_API_KEY=") and line.split("=", 1)[1].strip():
                    return line.split("=", 1)[1].strip().strip("\"'")
    raise RuntimeError("missing_api_key")


def json_default(obj):
    if hasattr(obj, "item"):
        return obj.item()
    return str(obj)


def ask_grok(opener, key, jpeg, status):
    user_content = [
        {
            "type": "text",
            "text": json.dumps(status, indent=2, default=json_default, allow_nan=False),
        },
        {
            "type": "image_url",
            "image_url": {
                "url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii"),
            },
        },
    ]
    payload = {
        "model": MODEL,
        "reasoning": {"effort": "low"},
        "max_tokens": 300,
        "parallel_tool_calls": False,
        "tool_choice": "required",
        "tools": rt.TOOLS,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user_content},
        ],
    }
    request = Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload, default=json_default).encode("utf-8"),
        method="POST",
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    with opener.open(request, timeout=30) as response:
        data = json.loads(response.read(500_000))

    choice = data["choices"][0]
    msg = choice["message"]
    thought = msg.get("reasoning") or msg.get("reasoning_content") or msg.get("content") or ""
    if not thought and msg.get("reasoning_details"):
        for rd in msg["reasoning_details"]:
            if rd.get("type") == "reasoning.text" and rd.get("text"):
                thought += rd["text"]

    calls = msg.get("tool_calls") or []
    if len(calls) != 1:
        raise RuntimeError("exactly_one_tool_call_required")
    function = calls[0]["function"]
    raw_args = function["arguments"]
    args = raw_args if isinstance(raw_args, dict) else json.loads(raw_args)
    return function["name"], args, data.get("id"), thought


def update_brain_monitor(step, max_steps, name, args, outcome, steps):
    """Write live status and latest frame info for user-facing viewing."""
    try:
        report_text = f"""# 🎥 DJI RoboMaster EP Core - Live Mission Monitor

**Live Video Stream**: [http://localhost:8088](http://localhost:8088) (MJPEG 30 FPS)
**Status**: Step {step}/{max_steps}
**Current Action**: `{name}({json.dumps(args)})`
**Outcome**: `{json.dumps(outcome)}`

---

### Step History
"""
        for s in steps:
            report_text += f"- **Step {s['step']}**: `{s['tool']}({json.dumps(s['args'])})` -> {json.dumps(s['outcome']['result'] if 'result' in s['outcome'] else s['outcome'])}\n"

        (BRAIN_DIR / "live_feed_status.md").write_text(report_text, encoding="utf-8")
    except Exception:
        pass


def generate_html_summary(log_dir, steps, result, calls):
    """Generate a clean visual timeline of photos, Grok reasoning, YOLO telemetry, and infrared ToF."""
    rows = ""
    for s in steps:
        step_num = s["step"]
        tool = s["tool"]
        args = json.dumps(s["args"])
        outcome = json.dumps(s["outcome"].get("result", s["outcome"]))
        thought = s.get("thought", "")
        tof = s.get("infraredLightMm")
        tof_str = f"{tof:.0f} mm ({tof/10.0:.1f} cm)" if tof is not None and tof > 0 else "None (Out of range / off-center)"
        yolo = s.get("yoloPosition") or {}
        if yolo.get("detected"):
            box_str = json.dumps(yolo.get("box"))
            score_val = float(yolo.get("score") or 0.0)
            dx_val = float(yolo.get("offsetFromCenterPx") or 0.0)
            cx_val = yolo.get("centerXPx", "N/A")
            by_val = yolo.get("bottomYPx", "N/A")
            dy_val = yolo.get("distanceToClawsPx", "N/A")
            yolo_str = f"""
            <div style="font-size: 12px; color: #58a6ff; font-weight: bold; margin-bottom: 4px;">🎯 YOLO Can Detected: {score_val:.2f}</div>
            <div style="font-size: 11px; font-family: monospace; color: #8b949e;">Box: {box_str}</div>
            <div style="font-size: 11px; font-family: monospace; color: #8b949e;">Center X: {cx_val} px (dx: {dx_val:+.0f} px)</div>
            <div style="font-size: 11px; font-family: monospace; color: #8b949e;">Bottom Y: {by_val} px (dy to claws: {dy_val} px)</div>
            <div style="font-size: 11px; font-family: monospace; color: {'#3fb950' if yolo.get('isInGraspRange') else ('#d29922' if yolo.get('isInCorridor') else '#f85149')};">
                Status: {'GRASP READY' if yolo.get('isInGraspRange') else ('IN CORRIDOR' if yolo.get('isInCorridor') else 'OFF-CENTER')}
            </div>
            """
        else:
            yolo_str = "<div style=\"font-size: 12px; color: #8b949e;\">No target detected</div>"

        img_name = f"step-{step_num:02d}.jpg"
        rows += f"""
        <tr style="border-bottom: 1px solid #30363d;">
            <td style="padding: 12px; font-weight: bold; color: #58a6ff; vertical-align: top;">Step {step_num}</td>
            <td style="padding: 12px; vertical-align: top;">
                <img src="{img_name}" style="width: 240px; border-radius: 8px; border: 1px solid #30363d;" alt="Step {step_num}">
            </td>
            <td style="padding: 12px; vertical-align: top; width: 220px;">
                <div style="font-size: 12px; color: #d2a8ff; font-weight: bold; margin-bottom: 4px;">📡 Infrared Laser ToF:</div>
                <div style="font-size: 12px; font-family: monospace; color: #c9d1d9; margin-bottom: 12px;">{tof_str}</div>
                {yolo_str}
            </td>
            <td style="padding: 12px; vertical-align: top;">
                <div style="font-family: monospace; color: #7ee787; font-size: 14px; margin-bottom: 6px;">{tool}({args})</div>
                {f'<div style="font-size: 12px; color: #8b949e; margin-bottom: 8px; font-style: italic;">Reasoning: {thought}</div>' if thought else ''}
                <div style="font-size: 11px; font-family: monospace; color: #c9d1d9; background: #161b22; padding: 6px; border-radius: 4px;">{outcome}</div>
            </td>
        </tr>
        """

    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Mission Post-Mortem Analysis - {log_dir.name}</title>
    <style>
        body {{ background: #0d1117; color: #c9d1d9; font-family: -apple-system, system-ui, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; padding: 24px; margin: 0; }}
        h1 {{ color: #58a6ff; font-size: 24px; margin-bottom: 8px; }}
        .header {{ background: #161b22; padding: 16px; border-radius: 8px; border: 1px solid #30363d; margin-bottom: 24px; }}
        table {{ width: 100%; border-collapse: collapse; background: #0d1117; }}
        th {{ text-align: left; padding: 12px; border-bottom: 2px solid #30363d; color: #8b949e; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>Autonomous Mission Metrics & Analysis</h1>
        <div><strong>Result:</strong> <span style="color: {'#2ea043' if result == 'STOPPED_BY_GROK' else '#f85149'}">{result}</span> | <strong>Grok Calls:</strong> {calls} | <strong>Run:</strong> {log_dir.name}</div>
    </div>
    <table>
        <thead>
            <tr><th>#</th><th>Camera Frame</th><th>Sensors (IR ToF & YOLO)</th><th>Action & Outcome</th></tr>
        </thead>
        <tbody>
            {rows}
        </tbody>
    </table>
</body>
</html>"""
    (log_dir / "summary.html").write_text(html, encoding="utf-8")
    (BRAIN_DIR / "summary.html").write_text(html, encoding="utf-8")


def main():
    LOG.mkdir(parents=True)
    BRAIN_DIR.mkdir(parents=True, exist_ok=True)
    key = load_key()
    opener = build_opener(HTTPSHandler(context=ssl.create_default_context()), NoRedirect())
    steps = []
    past_actions = []
    result = "INCOMPLETE"
    calls = 0

    print("Opening rover tools session...", flush=True)
    with rt.open_rover_tools() as tools:
        print("CONNECTED to robot. Telemetry:", tools.status(), flush=True)
        # Ensure arm is at elevated home view position and gripper starts open
        try:
            tools.reset_arm_and_gripper()
        except Exception:
            pass

        # Start camera & HTTP video broadcast on port 8088
        tools.start_camera(port=8088)
        # Open live stream in default browser so user can watch directly
        os.system("open http://localhost:8088 &")
        print("\n" + "="*60, flush=True)
        print("📺 LIVE STREAM BROADCASTING AT: http://localhost:8088", flush=True)
        print("="*60 + "\n", flush=True)

        last_outcome = None
        try:
            for step in range(1, MAX_STEPS + 1):
                # 1. Take photo
                jpeg = tools.frame_jpeg(quality=75)
                (LOG / f"step-{step:02d}.jpg").write_bytes(jpeg)
                # Mirror to brain directory for user viewing
                (BRAIN_DIR / "latest_rover_frame.jpg").write_bytes(jpeg)
                (BRAIN_DIR / f"rover_step_{step:02d}.jpg").write_bytes(jpeg)

                # 2. Build context
                current_tof = tools.tof_mm
                current_tracker = tools.latest_tracker
                status = {
                    "step": step,
                    "maxSteps": MAX_STEPS,
                    "robotStatus": tools.status(),
                    "lastActionOutcome": last_outcome,
                    "pastActionsSummary": past_actions[-4:],
                }

                # 3. Ask Grok to decide (with 1500 max tokens)
                print(f"\n[STEP {step}/{MAX_STEPS}] Querying Grok with photo...", flush=True)
                try:
                    name, args, request_id, thought = ask_grok(opener, key, jpeg, status)
                    calls += 1
                except Exception as exc:
                    print("GROK_ERROR", type(exc).__name__, str(exc)[:160], flush=True)
                    result = "GROK_ERROR"
                    break

                if thought:
                    print(f"[STEP {step}/{MAX_STEPS}] Grok Reasoning: {thought.strip()[:180]}...", flush=True)
                print(f"[STEP {step}/{MAX_STEPS}] Grok decided: {name}({json.dumps(args)}) [req: {request_id}]", flush=True)

                # 4. Execute decision on robot
                try:
                    exec_res = tools.dispatch(name, args)
                    last_outcome = {"tool": name, "result": exec_res}
                except rt.RoverError as exc:
                    last_outcome = {"tool": name, "error": str(exc)}
                except Exception as exc:
                    last_outcome = {"tool": name, "error": type(exc).__name__}
                    step_record = {
                        "step": step, "tool": name, "args": args, "outcome": last_outcome,
                        "thought": thought, "reqId": request_id, "infraredLightMm": current_tof,
                        "infraredLightCm": round(current_tof / 10.0, 1) if current_tof is not None else None,
                        "yoloPosition": current_tracker, "screenshotFile": f"step-{step:02d}.jpg"
                    }
                    steps.append(step_record)
                    print("HARDWARE_ERROR", last_outcome, flush=True)
                    result = "HARDWARE_ERROR"
                    break

                past_actions.append(f"{name}({json.dumps(args)})")
                step_record = {
                    "step": step, "tool": name, "args": args, "outcome": last_outcome,
                    "thought": thought, "reqId": request_id, "infraredLightMm": current_tof,
                    "infraredLightCm": round(current_tof / 10.0, 1) if current_tof is not None else None,
                    "yoloPosition": current_tracker, "screenshotFile": f"step-{step:02d}.jpg"
                }
                steps.append(step_record)
                print(f"[STEP {step}/{MAX_STEPS}] Executed: {json.dumps(last_outcome)}", flush=True)

                # Update live monitor in brain
                update_brain_monitor(step, MAX_STEPS, name, args, last_outcome, steps)

                # 5. Check if finished
                if name == "stop_robot":
                    result = "STOPPED_BY_GROK"
                    print("\n🎉 Grok called stop_robot. Mission complete!", flush=True)
                    break

        finally:
            try:
                print("Reverting arm and opening claws to bottom position...", flush=True)
                tools.reset_arm_and_gripper()
            except Exception:
                pass
            tools.stop("loop_finished")

    report = {"result": result, "grokCalls": calls, "steps": steps, "log": str(LOG)}
    (LOG / "report.json").write_text(json.dumps(report, indent=2))
    (BRAIN_DIR / "report.json").write_text(json.dumps(report, indent=2))

    metrics_export = {
        "result": result,
        "grokCalls": calls,
        "timestamp": time.time(),
        "steps": [
            {
                "step": s["step"],
                "screenshot": s["screenshotFile"],
                "infraredLightMm": s["infraredLightMm"],
                "infraredLightCm": s["infraredLightCm"],
                "yoloDetected": bool(s["yoloPosition"] and s["yoloPosition"].get("detected")),
                "yoloScore": s["yoloPosition"].get("score") if s["yoloPosition"] else None,
                "yoloBox": s["yoloPosition"].get("box") if s["yoloPosition"] else None,
                "yoloCenterXPx": s["yoloPosition"].get("centerXPx") if s["yoloPosition"] else None,
                "yoloBottomYPx": s["yoloPosition"].get("bottomYPx") if s["yoloPosition"] else None,
                "yoloOffsetFromCenterPx": s["yoloPosition"].get("offsetFromCenterPx") if s["yoloPosition"] else None,
                "yoloDistanceToClawsPx": s["yoloPosition"].get("distanceToClawsPx") if s["yoloPosition"] else None,
                "tool": s["tool"],
                "args": s["args"],
                "outcome": s["outcome"].get("result", s["outcome"]),
            }
            for s in steps
        ]
    }
    (LOG / "mission_metrics.json").write_text(json.dumps(metrics_export, indent=2))
    (BRAIN_DIR / "mission_metrics.json").write_text(json.dumps(metrics_export, indent=2))

    generate_html_summary(LOG, steps, result, calls)
    print(f"\nFINAL RESULT: {result} | Grok calls: {calls} | Saved report to {LOG}/report.json", flush=True)
    print(f"📊 Visual post-mortem analysis generated at: {LOG}/summary.html", flush=True)
    os._exit(0 if result == "STOPPED_BY_GROK" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
