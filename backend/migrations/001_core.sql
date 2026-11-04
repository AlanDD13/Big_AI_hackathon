-- Draft internal P3 storage. The API contracts and project access are separate.
BEGIN;

CREATE SCHEMA IF NOT EXISTS rover_backend;
REVOKE ALL ON SCHEMA rover_backend FROM PUBLIC;

CREATE TABLE rover_backend.ingest_events (
    producer text NOT NULL,
    event_id text NOT NULL,
    received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    PRIMARY KEY (producer, event_id)
);

CREATE TABLE rover_backend.objects (
    id uuid PRIMARY KEY,
    camera_id text NOT NULL,
    session_id text NOT NULL,
    track_id text NOT NULL,
    revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
    state jsonb NOT NULL CHECK (jsonb_typeof(state) = 'object'),
    UNIQUE (camera_id, session_id, track_id)
);

CREATE TABLE rover_backend.observations (
    producer text NOT NULL,
    event_id text NOT NULL,
    object_id uuid NOT NULL REFERENCES rover_backend.objects (id),
    observed_at timestamptz NOT NULL,
    data jsonb NOT NULL CHECK (jsonb_typeof(data) = 'object'),
    PRIMARY KEY (producer, event_id, object_id),
    FOREIGN KEY (producer, event_id)
        REFERENCES rover_backend.ingest_events (producer, event_id)
);

CREATE INDEX observations_by_object
    ON rover_backend.observations (object_id, observed_at);

CREATE TABLE rover_backend.classifications (
    object_id uuid PRIMARY KEY REFERENCES rover_backend.objects (id),
    status text NOT NULL CHECK (status IN ('pending', 'completed', 'unavailable')),
    data jsonb NOT NULL CHECK (jsonb_typeof(data) = 'object'),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE rover_backend.alerts (
    id uuid PRIMARY KEY,
    object_id uuid REFERENCES rover_backend.objects (id),
    transition_key text NOT NULL UNIQUE,
    kind text NOT NULL,
    data jsonb NOT NULL CHECK (jsonb_typeof(data) = 'object'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    acknowledged_at timestamptz
);

CREATE TABLE rover_backend.missions (
    id uuid PRIMARY KEY,
    object_id uuid NOT NULL REFERENCES rover_backend.objects (id),
    rover_id text NOT NULL,
    -- Idempotency scope comes from the authenticated caller or trusted producer.
    request_scope text NOT NULL,
    idempotency_key text NOT NULL,
    request_data jsonb NOT NULL CHECK (jsonb_typeof(request_data) = 'object'),
    command_id uuid UNIQUE,
    state text NOT NULL DEFAULT 'queued'
        CHECK (state IN ('queued', 'dispatching', 'running', 'holding', 'unknown',
                         'succeeded', 'failed', 'stopped', 'cancelled')),
    status_data jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(status_data) = 'object'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (request_scope, idempotency_key),
    CHECK (state NOT IN ('dispatching', 'running', 'holding', 'unknown')
        OR command_id IS NOT NULL)
);

-- The pending mission reserves its object. Only dispatching and execution
-- reserve the rover. An uncertain outcome retains both reservations.
CREATE UNIQUE INDEX one_live_mission_per_object
    ON rover_backend.missions (object_id)
    WHERE state IN ('queued', 'dispatching', 'running', 'holding', 'unknown');

CREATE UNIQUE INDEX one_active_mission_per_rover
    ON rover_backend.missions (rover_id)
    WHERE state IN ('dispatching', 'running', 'holding', 'unknown');

CREATE TABLE rover_backend.event_head (
    singleton boolean PRIMARY KEY CHECK (singleton),
    cursor bigint NOT NULL CHECK (cursor >= 0)
);
INSERT INTO rover_backend.event_head (singleton, cursor) VALUES (true, 0);

CREATE TABLE rover_backend.events (
    cursor bigint PRIMARY KEY CHECK (cursor > 0),
    kind text NOT NULL,
    entity_id uuid NOT NULL,
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

-- Perform ingest deduplication, state updates and event appends in one
-- transaction. Claim an input with INSERT ... ON CONFLICT DO NOTHING RETURNING
-- event_id. If no row is returned, compare the stored input before returning
-- the replay result or reporting an event-ID conflict.
--
-- Append after locking and updating business rows. The head row stays locked
-- until commit, so a reader cannot advance past an event from an uncommitted
-- transaction as it could with a sequence. A rollback also rolls back the head.
-- For a reconnect snapshot, read the entities and head in one REPEATABLE READ
-- transaction. Replay committed events ordered by cursor after that head.
CREATE FUNCTION rover_backend.append_event(event_kind text, event_entity uuid, event_payload jsonb)
RETURNS bigint
LANGUAGE sql
SET search_path = pg_catalog, pg_temp
AS $$
    WITH next_event AS (
        UPDATE rover_backend.event_head
        SET cursor = cursor + 1
        WHERE singleton
        RETURNING cursor
    )
    INSERT INTO rover_backend.events (cursor, kind, entity_id, payload)
    SELECT cursor, event_kind, event_entity, event_payload
    FROM next_event
    RETURNING cursor
$$;

REVOKE ALL ON ALL TABLES IN SCHEMA rover_backend FROM PUBLIC;
REVOKE ALL ON FUNCTION rover_backend.append_event(text, uuid, jsonb) FROM PUBLIC;

COMMIT;
