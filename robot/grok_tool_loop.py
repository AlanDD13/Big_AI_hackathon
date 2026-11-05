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

try:
    import rover_tools as rt
except ImportError:
    from robot import rover_tools as rt

MODEL_KEY = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("ROVER_MODEL", "grok")
MODEL = "x-ai/grok-4.7" if "grok" in MODEL_KEY.lower() else "google/gemini-3.8-flash"
MAX_STEPS = int(sys.argv[1]) if len(sys.argv) > 1 else 20
LOG = Path(os.environ.get("ROVER_LOG_DIR", "robot/.local/logs")) / f"tools-{time.time_ns()}"
BRAIN_DIR = Path(os.environ.get("ROVER_BRAIN_DIR", "/Users/alandd/.gemini/antigravity-cli/brain/dd744dcc-2fe5-4c81-8537-0ceabfe0ca93"))

SYSTEM = (
    "You are the autonomous pilot of a DJI RoboMaster EP Core rover equipped with omnidirectional mecanum wheels, "
    "a forward-facing camera, and a 2-DOF robotic arm with gripper jaws.\n"
    "Your objective: locate the metallic drink can standing upright on the open floor, approach it, center it squarely between the gripper jaws, "
    "grasp the can firmly with grasp_can(), lift it, transport it forward, place it back down, and finish.\n"
    "\n"
    "VISUAL SCENE NAVIGATION & HUD GUIDE:\n"
    "You have full visual intelligence to inspect the raw camera frame (640x360). Look directly at the photo!\n"
    "Do NOT confuse floor shadows, walls, or crumpled white paper with the target! The target is the physical upright metallic drink can.\n"
    "The camera frame has live HUD reference markings:\n"
    "1. CYAN VERTICAL LINE (x = 320): Physical centerline of the rover chassis and claws.\n"
    "2. GREEN VERTICAL LINES (x = 305 and 335): Claw pickup corridor. Center the can between these lines!\n"
    "3. GREY HORIZONTAL LINE (y = 220): Mid-range boundary (~35 cm away).\n"
    "4. YELLOW HORIZONTAL LINE (y = 280): Grasp zone boundary (~10-15 cm away).\n"
    "5. ORANGE HORIZONTAL LINE (y = 335): Gripper throat base line (physical base of claws).\n"
    "\n"
    "SENSOR NOTE:\n"
    "The infrared ToF laser is mounted OFF-CENTER and beams straight forward. When the can is centered at x=320, the laser misses the narrow can and reads the far wall (> 1000mm). Rely on your eyes for target distance!\n"
    "\n"
    "EXECUTION STRATEGY:\n"
    "\n"
    "PHASE 0: SCAN ROOM ONLY IF NO DRINK CAN IS VISIBLE IN FRAME:\n"
    "- If no drink can is visible anywhere in the room, rotate in place: turn_degrees(degrees=35.0) (or -35.0).\n"
    "- As soon as you spot the upright drink can in view, STOP SCANNING! Switch immediately to Phase 1/2!\n"
    "\n"
    "PHASE 1: CENTER & APPROACH (Can is in view, far or mid-range):\n"
    "- Check can horizontal center x in the image:\n"
    "  * If can is to the right of cyan line (x > 335): turn right! (e.g. if x=390, offset=+70px -> turn_degrees(degrees=10.5))\n"
    "  * If can is to the left of cyan line (x < 305): turn left! (e.g. if x=250, offset=-70px -> turn_degrees(degrees=-10.5))\n"
    "  Formula: degrees = (can_center_x - 320) * 0.15 deg/px.\n"
    "- Once centered between corridor lines (x around 310..330):\n"
    "  * If can is far (bottom y < 210): drive forward gently: drive_chassis(x_mps=0.08, duration_s=1.0) (~8 cm advance).\n"
    "  * If can is in mid-range (bottom y between 210 and 235): open claws if closed, creep forward: drive_chassis(x_mps=0.035, duration_s=0.5) (~1.7 cm creep).\n"
    "\n"
    "PHASE 2: EXECUTE GUARANTEED PHYSICAL GRASP (Can bottom y >= 230 or can entering claws):\n"
    "- AS SOON AS CAN IS IN GRASP RANGE (can bottom reaches y >= 230 or within ~10 cm of claws):\n"
    "  DO NOT DRIVE FORWARD SEPARATELY WITH drive_chassis! Driving forward separately will ram the can and kick it down!\n"
    "  CALL grasp_can(forward_duration_s=0.9) IMMEDIATELY!\n"
    "  grasp_can automatically:\n"
    "  1. Lowers arm to floor\n"
    "  2. Opens gripper claws wide (12 cm span) to funnel the can\n"
    "  3. Gently glides forward at 0.03 m/s for 0.9s (~2.7 cm) without tipping\n"
    "  4. Clamps gripper firmly at power 80\n"
    "  5. Lifts arm off the floor holding the can!\n"
    "\n"
    "PHASE 3: TRANSPORT, RELEASE, AND OBLIGATORY FINISH:\n"
    "- Once grasped and lifted (canTracker['canHeldInClaws'] or lastActionOutcome['tool'] == 'grasp_can'):\n"
    "  1. Transport forward: drive_chassis(x_mps=0.06, y_mps=0.0, duration_s=1.2)\n"
    "  2. Place can down: move_arm(dx_mm=0, dy_mm=-35)\n"
    "  3. Release gripper: control_gripper(state='open', power=50)\n"
    "  4. Finish: stop_robot(reason='can collected, carried, and placed successfully')\n"
    "  (stop_robot automatically resets arm to initial home elevation and opens claws!)\n"
    "\n"
    "RULES:\n"
    "- Output exactly ONE tool call per step.\n"
    "- Trust your eyes to locate the metallic drink can and ignore shadows and floor debris.\n"
    "- When can bottom reaches y >= 230, call grasp_can(0.9) immediately!"
)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError("redirect_rejected")


def load_key():
    if os.environ.get("OPENROUTER_API_KEY"):
        return os.environ["OPENROUTER_API_KEY"].strip().strip("\"'")
    for path in (Path("vision/.env"),
                 Path("robot/.env"),
                 Path(".env"),
                 Path("../vision/.env"),
                 Path("/Users/alandd/Desktop/Pavia_grok_hackathon/vision/.env"),
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
        "max_tokens": 1500,
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
    """Generate a clean visual timeline of photos, Grok reasoning, and movements for post-mortem analysis."""
    rows = ""
    for s in steps:
        step_num = s["step"]
        tool = s["tool"]
        args = json.dumps(s["args"])
        outcome = json.dumps(s["outcome"].get("result", s["outcome"]))
        thought = s.get("thought", "")
        img_name = f"step-{step_num:02d}.jpg"
        rows += f"""
        <tr style="border-bottom: 1px solid #30363d;">
            <td style="padding: 12px; font-weight: bold; color: #58a6ff; vertical-align: top;">Step {step_num}</td>
            <td style="padding: 12px; vertical-align: top;">
                <img src="{img_name}" style="width: 240px; border-radius: 8px; border: 1px solid #30363d;" alt="Step {step_num}">
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
        <h1>Mission Summary & Analysis</h1>
        <div><strong>Result:</strong> <span style="color: {'#2ea043' if result == 'STOPPED_BY_GROK' else '#f85149'}">{result}</span> | <strong>Grok Calls:</strong> {calls} | <strong>Run:</strong> {log_dir.name}</div>
    </div>
    <table>
        <thead>
            <tr><th>#</th><th>Camera Frame</th><th>Action & Outcome</th></tr>
        </thead>
        <tbody>
            {rows}
        </tbody>
    </table>
</body>
</html>"""
    (log_dir / "summary.html").write_text(html, encoding="utf-8")


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
                    steps.append({"step": step, "tool": name, "args": args, "outcome": last_outcome, "thought": thought})
                    print("HARDWARE_ERROR", last_outcome, flush=True)
                    result = "HARDWARE_ERROR"
                    break

                past_actions.append(f"{name}({json.dumps(args)})")
                steps.append({"step": step, "tool": name, "args": args, "outcome": last_outcome, "reqId": request_id, "thought": thought})
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
    generate_html_summary(LOG, steps, result, calls)
    print(f"\nFINAL RESULT: {result} | Grok calls: {calls} | Saved report to {LOG}/report.json", flush=True)
    print(f"📊 Visual post-mortem analysis generated at: {LOG}/summary.html", flush=True)
    os._exit(0 if result == "STOPPED_BY_GROK" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
