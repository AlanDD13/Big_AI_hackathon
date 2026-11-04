# Next backend slice

Finish or leave pull request #11 alone. Do not reimplement observation persistence. If `main` has moved and the PR no longer merges, rebase only after the human asks, then push the same branch.

## Start the next task from current main

After #11 is merged, fetch and branch from `origin/main`:

`task/zekihxh/mission-http`

Own `backend/**` only. One task on that branch.

The checkpoint is the first section of `backend/FIRST_SLICE_PROPOSAL.md`: one accepted mapped observation can become one queued collection mission through HTTP, using the existing `admit_collection` library and a fake mapping client. The fake robot records zero commands at this checkpoint.

Keep these rules:

- `POST /api/observations` still accepts only `MappingEvidence`. Demo sector bodies stay 422.
- `dispatchEligible` stays false until `mission_policy.collection_rejection` passes. Stationarity alone does not authorize dispatch.
- Persist the mission with the existing Postgres collection store when `BACKEND_DATABASE_URL` is set. Without that variable, an in-memory fake is enough for the unit test.
- Replay the same idempotency key and body. A different body for that key returns 409. Do not reserve an object twice.
- Do not call a rover, `:8004`, or the vision GPU. Do not add `/api/manual/*`.
- Do not apply migrations again. If the mission route needs a schema change, add a new migration file and stop before running it on the shared Supabase project. Ask Alan on the board first.
- Do not commit `.env`. Do not post tokens or connection strings.

## Tests required before the pull request

From the checkout root:

```powershell
& "C:\Users\Nurbol Karimov\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m unittest discover -s backend/tests -v
```

Add HTTP tests for the queue checkpoint in `FIRST_SLICE_PROPOSAL.md` section 4: valid request, duplicate key, conflicting key, missing or stale object, and a second request racing the same object. Existing tests must stay green. Postgres tests stay skipped unless `BACKEND_TEST_DATABASE_URL` points at a disposable database.

## After the tests pass

Commit, push `task/zekihxh/mission-http`, open a pull request to `main`, and post one board note:

`[zekihxh's Codex] READY: ... @AlanDD13 <PR url>`

Include the branch, commit, paths, tests, and that no robot call was made. Then stop. Manual chassis HTTP waits for a live bridge URL from `@platonyesin-dev` and for Alan's manual contract to be on the branch you implement.
