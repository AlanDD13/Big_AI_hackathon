# Backend and Grok — zekihxh

Own `backend/**`: API, mission policy/state, event stream, Grok adapter, storage
migrations, consumer clients, module dependencies, tests and local fakes.
Consume mapped observations/plans and dispatch missions to the bridge.
Coordinate shared wire-contract changes with AlanDD13.

The FastAPI process serves the draft dashboard stream and mapping observations.
It keeps that state in memory. Postgres migrations in `migrations/` are the
durable schema and are not applied to the shared Supabase project from this
process. Mission admission and dispatch coordination remain library calls
until that database is authorized.

## Run

From the repository root, with Python 3.12:

```powershell
python -m pip install -r backend/requirements.txt
$env:OPERATOR_TOKEN = "<at least 16 characters>"
$env:PEER_TOKEN = "<a different secret, at least 16 characters>"
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

`OPERATOR_TOKEN` and `PEER_TOKEN` stay in the environment. Do not commit them.
`BACKEND_COOKIE_SECURE=0` omits the Secure cookie flag for an http://localhost
demo. The default is Secure, matching the draft stream contract.

Operator routes use the `session` cookie from `POST /api/session`. Mapping
calls `POST /api/observations` with `Authorization: Bearer $PEER_TOKEN`.

## Test

```powershell
python -m unittest discover -s backend/tests -v
```

Postgres checks also need `BACKEND_TEST_DATABASE_URL` pointed at a disposable
database that has already had `migrations/` applied. Without it, those tests
skip. See `SUPABASE_SETUP.md`.
