# Big AI hackathon: rover object recovery

A camera detects an object, mapping locates it, the backend schedules a mission,
and the rover attempts a tested pickup and delivery. The dashboard shows objects,
rover position, mission progress and alerts.

Design: [FigJam plan](https://www.figma.com/board/bmW81xDifPQz5Icv98D8Hd/Big-AI?node-id=26-995).
This repository currently contains onboarding and coordination scaffolding;
the application and hardware integration are not implemented yet.

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

Existing coordination-tool checks:

```bash
python3 -m unittest discover -s tests -v
```

Module owners will add actual install/run/test commands to their own README files
as they implement their first slices.
