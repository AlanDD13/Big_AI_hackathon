-- Run after 001_core.sql in a disposable database. Test rows roll back.
BEGIN;

DO $$
DECLARE
    first_object uuid := '00000000-0000-0000-0000-000000000001';
    second_object uuid := '00000000-0000-0000-0000-000000000002';
    current_cursor bigint;
    original_cursor bigint;
    duplicate_constraint text;
BEGIN
    INSERT INTO rover_backend.ingest_events (producer, event_id, payload)
    VALUES ('test-vision', 'event-1', '{}');

    INSERT INTO rover_backend.ingest_events (producer, event_id, payload)
    VALUES ('test-vision', 'event-1', '{}')
    ON CONFLICT DO NOTHING;
    IF FOUND THEN
        RAISE EXCEPTION 'duplicate observation event was inserted';
    END IF;

    INSERT INTO rover_backend.objects (id, camera_id, session_id, track_id, state)
    VALUES (first_object, 'cam', 'session', 'track-1', '{}'),
           (second_object, 'cam', 'session', 'track-2', '{}');

    INSERT INTO rover_backend.missions
        (id, object_id, rover_id, request_scope, idempotency_key, request_data, command_id, state)
    VALUES (first_object, first_object, 'rover', 'operator', 'intent-1', '{}', first_object, 'unknown');

    BEGIN
        INSERT INTO rover_backend.missions
            (id, object_id, rover_id, request_scope, idempotency_key, request_data, command_id, state)
        VALUES (second_object, second_object, 'rover', 'operator', 'intent-2', '{}', second_object, 'running');
        RAISE EXCEPTION 'unknown mission failed to reserve rover slot';
    EXCEPTION WHEN unique_violation THEN
        GET STACKED DIAGNOSTICS duplicate_constraint = CONSTRAINT_NAME;
        IF duplicate_constraint <> 'one_active_mission_per_rover' THEN
            RAISE;
        END IF;
    END;

    BEGIN
        INSERT INTO rover_backend.missions
            (id, object_id, rover_id, request_scope, idempotency_key, request_data)
        VALUES (second_object, first_object, 'rover', 'operator', 'intent-2', '{}');
        RAISE EXCEPTION 'unknown mission failed to reserve object';
    EXCEPTION WHEN unique_violation THEN
        GET STACKED DIAGNOSTICS duplicate_constraint = CONSTRAINT_NAME;
        IF duplicate_constraint <> 'one_live_mission_per_object' THEN
            RAISE;
        END IF;
    END;

    INSERT INTO rover_backend.missions
        (id, object_id, rover_id, request_scope, idempotency_key, request_data)
    VALUES (second_object, second_object, 'rover', 'operator', 'intent-1', '{}')
    ON CONFLICT (request_scope, idempotency_key) DO NOTHING;
    IF FOUND THEN
        RAISE EXCEPTION 'same request key produced another mission';
    END IF;

    SELECT cursor INTO STRICT original_cursor FROM rover_backend.event_head;
    BEGIN
        PERFORM rover_backend.append_event('test.update', first_object, '{}');
        RAISE SQLSTATE 'Z0001' USING MESSAGE = 'test rollback';
    EXCEPTION WHEN SQLSTATE 'Z0001' THEN
        NULL;
    END;

    current_cursor := rover_backend.append_event('test.update', second_object, '{}');
    IF current_cursor IS DISTINCT FROM original_cursor + 1 THEN
        RAISE EXCEPTION 'event cursor did not roll back with its event';
    END IF;
    IF NOT EXISTS (
        SELECT FROM rover_backend.events
        WHERE cursor = current_cursor AND entity_id = second_object
    ) THEN
        RAISE EXCEPTION 'committed event missing from replay';
    END IF;
END;
$$;

ROLLBACK;
