# Next backend slice

Do not start one. Mission HTTP, vision ingest, and vision persistence are already on `origin/main` at `7fb18c6`. The old instruction in this file to branch `task/zekihxh/mission-http` is finished. Do not reimplement it.

## Wait for a named backend change

Read the board, then edit `backend/**` only when a new comment asks the backend to change behavior. A robot, vision, or mapping claim is not that request.

These are still refused:

- `/api/manual/*`, pickup, recovery, or any route that sends a robot command.
- Calling port `8004`, the vision server, or a DJI SDK session.
- Wiring draft PR #19 (`5730da7`) or the in-process physical adapter at `6a436f2` into this process.
- Treating `POST /api/missions`, a snapshot, Grok advice, a command ACK, or `visualPickupAdvisory` as permission to drive or pick up.
- Accepting `mapId demo-grid` with `calibrated: false` on `POST /api/observations`.
- Reporting or storing command `STOPPED` while the chassis stage is `UNKNOWN`.
- Resending `404 UNKNOWN_COMMAND`.
- A new migration, a shared-database write during a test, or a committed secret.

Recovery HTTP waits until `contracts/recovery-tools-v1.md` is an agreed physical contract and the bridge stops reporting command `STOPPED` while the chassis is `UNKNOWN`. The 300 ms fixture limit is not the arm-macro limit. Agree that macro separately before pickup or place.

## When a real slice starts

Fetch `origin/main` and branch `task/zekihxh/<short-task>` from it. Unset any upstream that points at `origin/main`. One task on that branch. Run:

```powershell
& "C:\Users\Nurbol Karimov\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m unittest discover -s backend/tests -v
```

Postgres tests stay skipped unless `BACKEND_TEST_DATABASE_URL` points at a disposable database. Commit, push `HEAD:task/zekihxh/<short-task>`, open a pull request to `main`, and post one board note:

`[zekihxh's Codex] READY: ... @AlanDD13 <PR url>`

Include the branch, commit, paths, tests, and that no robot call was made. Alan merges.
