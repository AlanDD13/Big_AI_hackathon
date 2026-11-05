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

def load_env_config():
    config = {}
    candidates = [
        Path(__file__).resolve().parent / ".env",
        Path("/Users/alandd/Desktop/Pavia_grok_hackathon/vision/.env"),
        Path("/Users/alandd/Desktop/Pavia_grok_hackathon/.env"),
        Path(".env"),
    ]
    for p in candidates:
        if p.is_file():
            for line in p.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().strip("\"'")
                    if k not in config:
                        config[k] = v
    return config

ENV = load_env_config()

# Model configured dynamically from environment / .env file
MODEL = (
    sys.argv[2].strip() if len(sys.argv) > 2 and sys.argv[2].strip()
    else os.environ.get("ROVER_MODEL")
    or ENV.get("ROVER_MODEL")
    or ENV.get("OPENROUTER_FAST_MODEL")
    or ENV.get("OPENROUTER_VISION_MODEL")
)
if not MODEL:
    raise RuntimeError("missing_model_in_env: please set ROVER_MODEL in your .env file")

MAX_STEPS = int(sys.argv[1]) if len(sys.argv) > 1 else 20
LOG = Path("/tmp/pavia-robot/logs") / f"tools-{time.time_ns()}"
BRAIN_DIR = Path("/Users/alandd/.gemini/antigravity-cli/brain/dd744dcc-2fe5-4c81-8537-0ceabfe0ca93")

SYSTEM = (
    "You are the autonomous pilot of a DJI RoboMaster EP Core rover equipped with omnidirectional mecanum wheels, "
    "a forward-facing camera, and a 2-DOF robotic arm with gripper jaws.\n"
    "Your objective: locate the metallic drink can standing upright on the open floor, approach it quickly, center it squarely between the gripper jaws, "
    "grasp the can firmly with grasp_can(), lift it, transport it forward, place it back down, and finish.\n"
    "\n"
    "REAL-TIME YOLO DETECTOR & ADAPTIVE OPTICAL RANGING (canTracker in robotStatus):\n"
    "The camera is processed in real time (30ms) by an onboard sub-pixel YOLO neural network (`canTracker` in `robotStatus`).\n"
    "The tracker calculates the EXACT metric distance and generates an ADAPTIVE plan every frame:\n"
    "- `canTracker['distanceToJawsCm']`: Remaining distance from extended open claw tips to the can in centimeters.\n"
    "- `canTracker['recommendedDrive']`: Exact adaptive speed and duration `{'x_mps': ..., 'duration_s': ...}` tailored specifically for current distance!\n"
    "- `canTracker['isInGraspRange']`: True whenever distanceToJawsCm <= 4.5cm or bottomYPx >= 285 (claws already frame the can!).\n"
    "\n"
    "ADAPTIVE RULES (ADAPTS TO ANY INITIAL DISTANCE - FAR, MID, OR CLOSE!):\n"
    "1. IF IN GRASP RANGE (distanceToJawsCm <= 4.5cm or isInGraspRange is True):\n"
    "   THE ROVER IS ALREADY AT THE CAN! CALL grasp_can() IMMEDIATELY! DO NOT DRIVE FORWARD SEPARATELY!\n"
    "2. IF OFF-CENTER (recommendedTurnDeg != 0):\n"
    "   Call turn_degrees(degrees=canTracker['recommendedTurnDeg']) to lock directly onto the center line.\n"
    "3. WHEN ALIGNED AND distanceToJawsCm > 4.5cm:\n"
    "   Execute the adaptive drive: call drive_chassis(x_mps=canTracker['recommendedDrive']['x_mps'], duration_s=canTracker['recommendedDrive']['duration_s'])!\n"
    "   - If far (> 28cm): drives rapidly (0.22 m/s) in 1 bold advance.\n"
    "   - If mid (14-28cm): drives smoothly (0.16 m/s).\n"
    "   - If close (<= 14cm): approaches gently (0.10 m/s) to land directly in grasp zone!\n"
    "   The rover will stop smoothly right between the open claws, and the very next action will be grasp_can()!\n"
    "3. TRANSPORT & FINISH (NEVER CALL grasp_can AGAIN ONCE LIFTED!):\n"
    "   Once grasp_can completes (can is held and lifted in claws):\n"
    "   - Step A: drive_chassis(x_mps=0.10, duration_s=1.2) (transport forward smoothly)\n"
    "   - Step B: move_arm(dx_mm=0, dy_mm=-35) (place upright on floor)\n"
    "   - Step C: control_gripper(state='open', power=50) (release can safely upright)\n"
    "   - Step D: stop_robot(reason='task completed successfully')!\n"
    "   NEVER call grasp_can again once the can is lifted! Proceed immediately to Step A (drive_chassis)!\n"
    "\n"
    "RULES:\n"
    "- Output exactly ONE tool call per step.\n"
    "- Trust canTracker distanceToJawsCm and maxSafeAdvanceM so the rover never overshoots.\n"
    "- When aligned on line (isInCorridor is True), DO NOT TURN! Advance forward boldly in 1 rapid step!\n"
    "- When distanceToJawsCm <= 9 cm (or isInGraspRange is True), call grasp_can() immediately!\n"
    "- Once placed safely on floor, call stop_robot() immediately!"
)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError("redirect_rejected")


def load_key():
    key = os.environ.get("OPENROUTER_API_KEY") or ENV.get("OPENROUTER_API_KEY")
    if key:
        return key
    raise RuntimeError("missing_api_key: please set OPENROUTER_API_KEY in your .env file")


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
        "max_tokens": int(ENV.get("ROVER_MAX_TOKENS", 800)),
        "parallel_tool_calls": False,
        "tool_choice": "required",
        "tools": rt.TOOLS,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user_content},
        ],
    }
    effort = os.environ.get("OPENROUTER_REASONING_EFFORT") or ENV.get("OPENROUTER_REASONING_EFFORT")
    if effort and effort.lower() != "none":
        payload["reasoning"] = {"effort": effort}
    elif "grok" in MODEL.lower():
        payload["reasoning"] = {"effort": "low"}
    request = Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload, default=json_default).encode("utf-8"),
        method="POST",
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    for attempt in range(3):
        try:
            with opener.open(request, timeout=30) as response:
                data = json.loads(response.read(500_000))
        except Exception as exc:
            if attempt < 2:
                time.sleep(1.0 * (attempt + 1))
                continue
            raise

        choice = data["choices"][0]
        msg = choice["message"]
        thought = msg.get("reasoning") or msg.get("reasoning_content") or msg.get("content") or ""
        if not thought and msg.get("reasoning_details"):
            for rd in msg["reasoning_details"]:
                if rd.get("type") == "reasoning.text" and rd.get("text"):
                    thought += rd["text"]

        calls = msg.get("tool_calls") or []
        if len(calls) >= 1:
            function = calls[0]["function"]
            raw_args = function["arguments"]
            args = raw_args if isinstance(raw_args, dict) else json.loads(raw_args)
            return function["name"], args, data.get("id"), thought

        # Fallback: check if content contains a JSON tool call
        content = msg.get("content") or ""
        if "{" in content and "}" in content:
            try:
                start = content.index("{")
                end = content.rindex("}") + 1
                cand = json.loads(content[start:end])
                if "name" in cand:
                    args = cand.get("arguments") or cand.get("parameters") or {}
                    if isinstance(args, str):
                        args = json.loads(args)
                    return cand["name"], args, data.get("id"), thought
            except Exception:
                pass

        if attempt < 2:
            time.sleep(0.5)
            continue
        print("DEBUG FULL DATA:", json.dumps(data), flush=True)
        raise RuntimeError(f"no_tool_call_in_response: {content[:100]}")


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

    print(f"Loaded model from .env: {MODEL}", flush=True)
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
                jpeg = tools.frame_jpeg(quality=65)
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
