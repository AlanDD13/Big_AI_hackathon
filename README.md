# Big AI hackathon: rover object recovery

A small team is building a rover that finds an object, asks Grok what it is and
whether it can be picked up, and then either collects it or tells a person
where to collect it. The system analyzes the object's position and guides the
rover to it. The operator watches two cameras and a spoken message feed.

Design: [FigJam plan](https://www.figma.com/board/bmW81xDifPQz5Icv98D8Hd/Big-AI?node-id=26-995).
Repository: [AlanDD13/Big_AI_hackathon](https://github.com/AlanDD13/Big_AI_hackathon) (private).

## What the operator sees

The console is three panels:

| Panel | What it shows |
| --- | --- |
| Fixed camera | The overhead or room camera that first sees the object. |
| Rover camera | The camera on the rover, used while it approaches and picks up. |
| Rover messages | A chat of progress, spoken aloud when voice is on. |

The messages follow the recovery:

1. **Object found.** The fixed camera has a stationary track.
2. **Classified as…** Grok names the object.
3. **Going to it.** The rover is being guided to the object's position.
4. **Grabbed.** The pickup command sequence finished.
5. **Finished.** The rover returned, lowered, released, and raised the empty arm.
6. **Needs a person.** The object is unsupported or too uncertain to grab. The message tells the operator to pick it up in a demo sector, such as sector B2.

Voice uses the Grok text-to-speech API on the backend. The browser plays the
audio the backend returns. Historical messages stay silent after a reconnect;
only new messages are spoken, and only after the operator turns voice on.
Missing cameras, a missing API key, and speech failures stay visible as waiting
or unavailable states. The page does not invent a detection or a completed grab.

## How a recovery works

```text
fixed camera
    → vision tracks the object and Grok classifies it
    → the object's position is analyzed on the floor
    → that position guides the rover to the object
    → the rover camera is used for the pickup
    → if it cannot be grabbed, the operator is told the sector
```

Local detection and tracking run on the vision machine. The floor position
comes from the camera view and mapping. That position is what guides the rover
to the object. People, animals, carried objects, and uncertain subjects stay
out of pickup. If the object cannot be grabbed, the operator gets a sector
message instead of a drive command.

## The five parts

Each person owns one area. AlanDD13 is the human lead and merges pull requests.
Shared contracts, integration notes, and this repository’s coordination files
are also Alan’s.

| Area | Person | Directory | Responsibility |
| --- | --- | --- | --- |
| Dashboard | LcyShox | [frontend/](frontend/README.md) | Operator console: both cameras, rover messages, voice playback |
| Detection and tracking | AlanDD13 | [vision/](vision/README.md) | Camera ingest, local detection, stable tracks, Grok classification |
| Backend and Grok | zekihxh | [backend/](backend/README.md) | Sessions, stored events, mission policy, camera proxies, speech |
| Robot execution | platonyesin-dev | [robot/](robot/README.md) | The only DJI RoboMaster session, motion, arm, and gripper |
| Mapping and routes | Tezqx | [mapping/](mapping/README.md) | Floor position, rover pose, and planned routes |

Vision serves frames. Mapping turns pixels into floor coordinates when the
room is calibrated. The backend decides what may be queued or announced. The
robot is the only process that opens the DJI SDK, and only one machine on the
network may hold that session. The dashboard talks to the backend. It never
holds a camera password, a database role, or the xAI key.

Durable hackathon data lives in the Postgres schema `rover_backend` on the
selected Supabase project. That schema is separate from other applications in
the same project. The browser never connects to the database.

## Where Grok is used

Every live Grok call goes to `https://api.x.ai/v1` with `XAI_API_KEY`. The key
stays on the server that makes the call.

| Job | Default model or voice | Where |
| --- | --- | --- |
| Name the object and estimate whether it can be grabbed | `grok-4.7` | `vision/classification/` |
| Watch the rover camera and run the pickup tools | `grok-4.7` | `robot/grok_tool_loop.py` |
| Optional recovery policy suggestion | `grok-4.7` | `integration/recovery/policy.py` |
| Speak the operator messages | Grok voice `eve`, English | `backend/speech.py` |

Without the API key, classification and speech stay unavailable. The software
does not substitute another provider or fill in a pretend result. Offline tests
mock the API, so a green test run is not evidence of a live Grok call.
[docs/GROK_MODELS.md](docs/GROK_MODELS.md) records the exact endpoints and what
“used” means.

## Run the operator console

1. Install backend dependencies with `python -m pip install -r backend/requirements.txt`.
2. Configure the server environment from [`.env.example`](.env.example): distinct
   operator and peer tokens, the native xAI key, and the fixed and rover camera
   source URLs. For local HTTP, set `BACKEND_COOKIE_SECURE=0`.
3. Run `python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000`.
4. Copy `frontend/.env.example` to a private `frontend/.env`, set the same
   operator token and backend URL, then run `npm --prefix frontend ci` and
   `npm --prefix frontend run dev`. Open the printed URL and turn voice on.
5. For camera-triggered recovery, start the [vision service](vision/README.md)
   with a private `VISION_RECOVERY_CONFIG` based on
   [`vision/camera-recovery.example.json`](vision/camera-recovery.example.json).
   Confirm the tested physical arrangement before setting `setupConfirmed`.

The shell or process runner has to load the backend environment. Placing a
root `.env` file in the repository does not load it by itself.

Module install and run notes:

- [frontend/README.md](frontend/README.md) — operator console
- [vision/README.md](vision/README.md) — detection, tracking, classification
- [backend/README.md](backend/README.md) — API, storage, speech
- [robot/README.md](robot/README.md) — DJI SDK, motion, arm, and gripper
- [mapping/README.md](mapping/README.md) — localization and route planning

## Checks

Offline checks use each module’s own environment:

```bash
python -m pytest vision/tests tests -q
python -m unittest discover -s backend/tests -q
python -m unittest discover -s robot/tests -q
python -m pytest integration/recovery/tests -q
npm --prefix frontend test
npm --prefix frontend run build
```

Postgres integration tests need a disposable test database and skip when that
URL is absent. These commands do not move the rover.

## Team and further reading

Start with [teammate onboarding](docs/ONBOARDING.md). Shared rules are in
[AGENTS.md](AGENTS.md). Draft interfaces are in [docs/CONTRACTS.md](docs/CONTRACTS.md).
The operator wire format is [contracts/operator-console-v1.md](contracts/operator-console-v1.md).
Coordination uses the [pinned message board](https://github.com/AlanDD13/Big_AI_hackathon/issues/1)
through `tools/board`; [docs/TEAM_SETUP.md](docs/TEAM_SETUP.md) explains that tooling.

Only AlanDD13 merges pull requests. A change to someone else’s directory needs
a handoff from that owner first.
