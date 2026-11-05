# Big AI hackathon: rover object recovery

A rover object recovery system with a two-camera operator console, native xAI
classification and grabbability estimates, measured route execution, and spoken
progress messages.

The current console shows the fixed camera, rover camera and live backend
messages. Stationary objects are assessed by Grok. An eligible can with an
estimate of at least 80% can trigger the explicitly armed demo route: 30 cm
forward, right 90°, 154 cm forward, Grok-guided pickup, return, lower and release.
Unsupported or uncertain objects produce an operator notification. Repeated
detections do not repeatedly launch the same recovery.

The route requires the confirmed physical setup and starting pose. General
navigation still depends on mapping/calibration and its separate mission gates.
Command acknowledgements do not independently verify holding or placement.

Design: [FigJam plan](https://www.figma.com/board/bmW81xDifPQz5Icv98D8Hd/Big-AI?node-id=26-995).
The application and measured route are implemented. Native xAI requests require
`XAI_API_KEY`; offline tests and the labelled browser integration fixture do not
prove live Grok inference or camera/hardware acceptance for a changed setup.

## Run the operator console

1. Install backend dependencies with `python -m pip install -r backend/requirements.txt`.
2. Configure server environment using [`.env.example`](.env.example): distinct
   operator/peer tokens, native xAI key, and fixed/rover camera source URLs.
   For local HTTP only, use `BACKEND_COOKIE_SECURE=0`.
3. Run `python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000`.
4. Copy `frontend/.env.example` to private `frontend/.env`, set the matching
   operator token and backend URL, then run `npm --prefix frontend ci` and
   `npm --prefix frontend run dev`. Open the printed URL and enable voice.
5. For camera-triggered recovery, run the [vision service](vision/README.md)
   with a private `VISION_RECOVERY_CONFIG` based on
   [`vision/camera-recovery.example.json`](vision/camera-recovery.example.json).
   Confirm the tested physical arrangement before setting `setupConfirmed`.

Backend environment must be loaded by the shell/process runner; copying a root
`.env` alone does not load it. The UI shows waiting/unavailable states when a
service or key is absent. It does not fabricate live detections or progress.

See [Grok models and usage evidence](docs/GROK_MODELS.md),
[operator console API](contracts/operator-console-v1.md), and
[camera recovery gates](contracts/camera-recovery-v1.md).

| Area | Owner | Exclusive directory |
| --- | --- | --- |
| Dashboard | `LcyShox` | [frontend/](frontend/README.md) |
| Detection and tracking | `AlanDD13` | [vision/](vision/README.md) |
| Backend and Grok | `zekihxh` | [backend/](backend/README.md) |
| Robot execution | `platonyesin-dev` | [robot/](robot/README.md) |
| Mapping and route planning | `Tezqx` | [mapping/](mapping/README.md) |

`AlanDD13` is the human lead and shared-file owner. Everyone owns their module's
tests, mocks and dependencies. Only the human lead merges PRs.

**Start here: [teammate onboarding and messages](docs/ONBOARDING.md).**
Read [agent rules](AGENTS.md) and [draft contracts](docs/CONTRACTS.md) before work.
Coordination uses the [pinned Message Board](https://github.com/AlanDD13/Big_AI_hackathon/issues/1)
through `tools/board`; [detailed setup](docs/TEAM_SETUP.md) explains the tooling.

Offline checks (use the module's Python environment/dependencies):

```bash
python -m pytest vision/tests tests -q
python -m unittest discover -s backend/tests -q
python -m unittest discover -s robot/tests -q
python -m pytest integration/recovery/tests -q
npm --prefix frontend test
npm --prefix frontend run build
```

Postgres integration tests require a separately configured disposable test
database; they skip when it is absent. These checks do not move physical hardware.
