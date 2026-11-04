# Supabase Postgres handoff

AlanDD13 selected the existing project buojgmoumyzqxmerckjm in eu-west-1. Read-only catalog access and runtime/migration access assignments are pending. P3 owns the private
backend schema and migration files. Shared wire schemas remain lead-owned.

## Proposed environment variables

| Name | Used by | Value |
| --- | --- | --- |
| BACKEND_DATABASE_URL | Backend service only | TLS Postgres connection for the dedicated runtime role |
| BACKEND_MIGRATION_DATABASE_URL | Authorized migration runner only | TLS Postgres connection for the schema owner |
| BACKEND_INVENTORY_DATABASE_URL | P3 preflight only | TLS connection for the individually scoped read-only identity |
| BACKEND_TEST_DATABASE_URL | Local backend tests | Connection to a disposable Postgres database |
| BACKEND_BASE_URL | Frontend, mapping and robot integrations | Backend API address |

Use the connection information from the selected project's Connect panel.
For a persistent backend, use a direct connection or the session pooler on
IPv4-only networks. Use verified TLS. The runtime role should have only
the privileges needed to read and write backend state. Supply credentials
through the team's approved secret channel. Browser, mapping and robot
integrations use the backend API with their assigned credentials.

## Storage and integration checks

Backend writes deduplication records, state changes and outgoing events in the
same transaction. Use a consistent database snapshot for the REST view and its
SSE watermark, then replay committed events after that watermark. Verify that
concurrent commits cannot make a lower cursor visible after a higher cursor.
Use a separate test database for retries, rollback and concurrency checks.

Before applying migrations to the team project, confirm the project reference,
schema owner and runtime role with AlanDD13. Verify the migration and constraints
in a disposable Postgres database first. The mapping, vision and robot wire
fixtures remain pending review.

Apply the draft schema to a disposable Postgres database and run the storage
smoke test from the workspace root:

```powershell
psql $env:BACKEND_TEST_DATABASE_URL -v ON_ERROR_STOP=1 -f backend/migrations/001_core.sql
psql $env:BACKEND_TEST_DATABASE_URL -v ON_ERROR_STOP=1 -f backend/migrations/002_enqueue.sql
psql $env:BACKEND_TEST_DATABASE_URL -v ON_ERROR_STOP=1 -f backend/migrations/003_claim_dispatch.sql
psql $env:BACKEND_TEST_DATABASE_URL -v ON_ERROR_STOP=1 -f backend/migrations/004_dispatch_outcome.sql
psql $env:BACKEND_TEST_DATABASE_URL -v ON_ERROR_STOP=1 -f backend/migrations/005_collection_plan.sql
psql $env:BACKEND_TEST_DATABASE_URL -v ON_ERROR_STOP=1 -f backend/migrations/006_observation_frames.sql
psql $env:BACKEND_TEST_DATABASE_URL -v ON_ERROR_STOP=1 -f backend/tests/storage_smoke.sql
psql $env:BACKEND_TEST_DATABASE_URL -v ON_ERROR_STOP=1 -f backend/tests/enqueue_smoke.sql
psql $env:BACKEND_TEST_DATABASE_URL -v ON_ERROR_STOP=1 -f backend/tests/claim_dispatch_smoke.sql
psql $env:BACKEND_TEST_DATABASE_URL -v ON_ERROR_STOP=1 -f backend/tests/dispatch_outcome_smoke.sql
```

The smoke test rolls back its rows. It covers input deduplication, mission
idempotency, reservations during unknown execution, and rollback-safe event
cursors. Verify concurrent connections separately before shared rollout.

## Inspect the selected project

Use individually scoped access to inventory schemas and relations before
migration review. This query reads catalog metadata and returns no application
rows. If rover_backend already exists, compare its schema and migration history
with the owner before proceeding.

```powershell
psql $env:BACKEND_INVENTORY_DATABASE_URL -v ON_ERROR_STOP=1 -f backend/tests/inventory_read_only.sql
```

## Local verification

The inventory, core migration and smoke test passed in a disposable
PostgreSQL 18 cluster. Three independent sessions verified that an
uncommitted cursor stays invisible, a second writer cannot bypass the
cursor lock, and a repeatable-read snapshot can replay the next event
after it closes. The selected Supabase project still needs its own
read-only inventory and migration review.

Run the pure backend checks from the workspace root:

```powershell
python -m unittest discover -s backend/tests -v
```

These cover stationarity fixtures and structured Grok intent parsing. The
intent decoder leaves ID lookup, authorization and mission gating to its
caller. It uses local action names pending the adapter contract.

Concurrent enqueue verification also passed on PostgreSQL 18. Two
competing connections contended with an uncommitted reservation. After
commit, the same key replayed the existing mission and a different key
returned object_reserved. The database contained one mission and one event.



With BACKEND_TEST_DATABASE_URL set to the migrated disposable database, install
Psycopg 3 in the test environment and run the Python checks again. The database
integration test commits fixture rows as it exercises separate transactions.
Use a fresh database for the suite.

```powershell
python -m pip install "psycopg[binary]==3.3.8"
python -m unittest discover -s backend/tests -v
```

On PostgreSQL 18, all four SQL smoke tests and 17 Python tests passed.
The dispatch integration test observed the committed command in a separate
connection before the fake bridge send, persisted unknown after a timeout,
then used a fresh store to reconcile the original command without resending.


The collection adapter passed three additional PostgreSQL 18 checks. It stores
the plan reference in the enqueue transaction, replays an existing key before
checking freshness, and rejects evidence that changes or ages while mapping
plans the route. The full suite now passes 20 Python tests and four SQL smoke
tests.


The observation store passed five additional PostgreSQL checks. Exact event
and frame retries reuse the object without increasing its revision. Conflicting
or older evidence is rejected. The integration fixture stores a complete
stationary episode, seeds the approved classification and pickup profile, and
queues a mission through the collection adapter with fake mapping. The full
local suite passes 25 Python tests and four SQL smoke tests.
