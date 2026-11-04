-- Run after 001_core.sql and 002_enqueue.sql in a disposable database.
BEGIN;

DO $$
DECLARE
    target uuid := '00000000-0000-0000-0000-000000000011';
    other_target uuid := '00000000-0000-0000-0000-000000000012';
    initial_cursor bigint;
    queued_id uuid;
    result record;
BEGIN
    INSERT INTO rover_backend.objects (id, camera_id, session_id, track_id, state)
    VALUES (target, 'cam', 'session', 'track-1', '{}'),
           (other_target, 'cam', 'session', 'track-2', '{}');
    SELECT cursor INTO STRICT initial_cursor FROM rover_backend.event_head;

    SELECT * INTO STRICT result FROM rover_backend.enqueue_mission(
        target, 'rover-a', 'operator', 'intent-1', '{"destination":"bin-a"}'
    );
    IF result.outcome <> 'created' OR result.mission_id IS NULL
       OR result.mission_state <> 'queued' THEN
        RAISE EXCEPTION 'first enqueue did not create a queued mission';
    END IF;
    queued_id := result.mission_id;

    SELECT * INTO STRICT result FROM rover_backend.enqueue_mission(
        target, 'rover-b', 'operator', 'intent-1', '{"destination":"bin-a"}'
    );
    IF result.outcome <> 'replayed' OR result.mission_id IS DISTINCT FROM queued_id
       OR result.mission_state <> 'queued' THEN
        RAISE EXCEPTION 'same request failed to replay original mission';
    END IF;

    SELECT * INTO STRICT result FROM rover_backend.enqueue_mission(
        target, 'rover-a', 'operator', 'intent-1', '{"destination":"bin-b"}'
    );
    IF result.outcome <> 'key_conflict' OR result.mission_id IS NOT NULL THEN
        RAISE EXCEPTION 'changed request did not produce a key conflict';
    END IF;

    SELECT * INTO STRICT result FROM rover_backend.enqueue_mission(
        other_target, 'rover-a', 'operator', 'intent-1', '{"destination":"bin-a"}'
    );
    IF result.outcome <> 'key_conflict' THEN
        RAISE EXCEPTION 'changed object did not produce a key conflict';
    END IF;

    SELECT * INTO STRICT result FROM rover_backend.enqueue_mission(
        target, 'rover-a', 'another-operator', 'intent-1', '{"destination":"bin-a"}'
    );
    IF result.outcome <> 'object_reserved' OR result.mission_id IS NOT NULL THEN
        RAISE EXCEPTION 'second operator was able to reserve the same object';
    END IF;

    UPDATE rover_backend.missions SET state = 'cancelled' WHERE id = queued_id;
    SELECT * INTO STRICT result FROM rover_backend.enqueue_mission(
        target, 'rover-a', 'operator', 'intent-1', '{"destination":"bin-a"}'
    );
    IF result.outcome <> 'replayed' OR result.mission_id IS DISTINCT FROM queued_id
       OR result.mission_state <> 'cancelled' THEN
        RAISE EXCEPTION 'replay lost the original terminal mission state';
    END IF;

    BEGIN
        SELECT * INTO STRICT result FROM rover_backend.enqueue_mission(
            target, 'rover-a', 'another-operator', 'intent-1', '{"destination":"bin-a"}'
        );
        IF result.outcome <> 'created' OR result.mission_id IS NULL
           OR result.mission_id = queued_id THEN
            RAISE EXCEPTION 'terminal mission did not release the reservation';
        END IF;
        RAISE SQLSTATE 'Z0001' USING MESSAGE = 'test rollback';
    EXCEPTION WHEN SQLSTATE 'Z0001' THEN
        NULL;
    END;

    IF (SELECT count(*) FROM rover_backend.missions WHERE object_id = target) <> 1
       OR (SELECT cursor FROM rover_backend.event_head) <> initial_cursor + 1
       OR NOT EXISTS (
           SELECT FROM rover_backend.events
           WHERE cursor = initial_cursor + 1 AND kind = 'mission.queued' AND entity_id = queued_id
             AND payload = jsonb_build_object('state', 'queued', 'objectId', target, 'roverId', 'rover-a')
       ) THEN
        RAISE EXCEPTION 'enqueue did not persist exactly one matching mission and queue event';
    END IF;
    IF EXISTS (
        SELECT FROM rover_backend.missions
        WHERE id = queued_id AND (command_id IS NOT NULL OR rover_id <> 'rover-a')
    ) THEN
        RAISE EXCEPTION 'enqueue or replay changed physical assignment or command state';
    END IF;
END;
$$;

ROLLBACK;
