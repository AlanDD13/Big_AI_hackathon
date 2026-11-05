# Next backend slice

Do not start a new feature. Mission HTTP, vision ingest, vision persistence, the camera proxy, and manual forwarding are already on `origin/main` at `96e7b0f`. The old instruction to branch `task/zekihxh/mission-http` is finished.

## What is already in review

Leave these branches alone unless a review comment asks for a backend change. If it does, commit on that same branch. Do not open a second pull request for the same fix.

- PR #33 (`task/zekihxh/demo-sector`, `2040e8d`): display-only `demoSector`. Not an observation. `dispatchEligible` stays false.
- PR #36 (`task/zekihxh/manual-gate-latency`, `88cb374`): per-drive exclusion check on one reused connection, and `409 INTENT_EXPIRED` when the tick dies during that check. The lease stays. The sequence cannot be replayed.
- PR #35 (`task/zekihxh/board-tests`, `18fccd2`): Windows `tests/test_board.py` only.
- PR #34 is Alan's 100 ms clock allowance. Do not edit `backend/manual_requests.py` or `robot/safety.py`.

`frontend/src/manualBackend.ts` still drops a hold on any rejection and on a round trip over 250 ms. That is LcyShox's file. A held direction stays one nudge until that client treats `INTENT_EXPIRED` as a missed tick.

## Still refused

- A new `/api/manual/*` action, pickup, recovery HTTP, or any route that sends a robot command.
- Calling port `8004`, the vision server, or a DJI SDK session. Starting or reloading `192.168.5.126:8000`.
- Wiring PR #19's simulator loop, or the physical adapter, into this process.
- Treating `POST /api/missions`, a snapshot, Grok advice, a command ACK, or `visualPickupAdvisory` as permission to drive or pick up.
- Accepting `mapId demo-grid` with `calibrated: false` on `POST /api/observations`.
- Implementing `task/AlanDD13/route-pickup-return`. That is `robot/**`.
- Reporting or storing command `STOPPED` while the chassis stage is `UNKNOWN`.
- Resending `404 UNKNOWN_COMMAND`.
- A new migration, a shared-database write during a test, or a committed secret.
- Restarting the Cursor board watcher.

## When a real slice starts

Fetch `origin/main` and branch `task/zekihxh/<short-task>` from it. Unset any upstream that points at `origin/main`. One task on that branch. Run:

```powershell
& "C:\Users\Nurbol Karimov\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m unittest discover -s backend/tests -v
```

Postgres tests stay skipped unless `BACKEND_TEST_DATABASE_URL` points at a disposable database. Commit with the `Noxardi` author environment, push `HEAD:task/zekihxh/<short-task>`, open a pull request to `main`, and post one board note:

`[zekihxh's Codex] READY: ... @AlanDD13 <PR url>`

Include the branch, commit, paths, tests, and that no robot call was made. Alan merges.
