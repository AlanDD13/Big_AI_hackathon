-- Run after the core, enqueue, and claim migrations in a disposable database.
BEGIN;

DO $$
DECLARE
    target uuid := '00000000-0000-0000-0000-000000000021';
    other_target uuid := '00000000-0000-0000-0000-000000000022';
    cancelled_target uuid := '00000000-0000-0000-0000-000000000023';
    first_mission uuid;
    second_mission uuid;
    cancelled_mission uuid;
    first_command uuid;
    initial_cursor bigint;
    result record;
BEGIN
    INSERT INTO rover_backend.objects (id, camera_id, session_id, track_id, state)
    VALUES (target, 'cam', 'session', 'track-1', '{}'),
           (other_target, 'cam', 'session', 'track-2', '{}'),
           (cancelled_target, 'cam', 'session', 'track-3', '{}');
    SELECT mission_id INTO STRICT first_mission FROM rover_backend.enqueue_mission(
        target, 'rover', 'operator', 'intent-1', '{}'
    );
    SELECT mission_id INTO STRICT second_mission FROM rover_backend.enqueue_mission(
        other_target, 'rover', 'operator', 'intent-2', '{}'
    );
    SELECT mission_id INTO STRICT cancelled_mission FROM rover_backend.enqueue_mission(
        cancelled_target, 'rover', 'operator', 'intent-3', '{}'
    );
    UPDATE rover_backend.missions SET state = 'cancelled' WHERE id = cancelled_mission;
    SELECT cursor INTO STRICT initial_cursor FROM rover_backend.event_head;

    SELECT * INTO STRICT result FROM rover_backend.claim_dispatch(target);
    IF result.outcome IS DISTINCT FROM 'not_found' OR result.command_id IS NOT NULL THEN
        RAISE EXCEPTION 'unknown mission returned a dispatch claim';
    END IF;

    SELECT * INTO STRICT result FROM rover_backend.claim_dispatch(cancelled_mission);
    IF result.outcome IS DISTINCT FROM 'not_queued'
       OR result.mission_state IS DISTINCT FROM 'cancelled' OR result.command_id IS NOT NULL THEN
        RAISE EXCEPTION 'cancelled mission returned a dispatch claim';
    END IF;

    BEGIN
        SELECT * INTO STRICT result FROM rover_backend.claim_dispatch(first_mission);
        IF result.outcome IS DISTINCT FROM 'claimed' THEN
            RAISE EXCEPTION 'first queued mission failed to claim';
        END IF;
        RAISE SQLSTATE 'Z0001' USING MESSAGE = 'test rollback';
    EXCEPTION WHEN SQLSTATE 'Z0001' THEN
        NULL;
    END;

    SELECT * INTO STRICT result FROM rover_backend.claim_dispatch(first_mission);
    IF result.outcome IS DISTINCT FROM 'claimed'
       OR result.mission_state IS DISTINCT FROM 'dispatching' OR result.command_id IS NULL THEN
        RAISE EXCEPTION 'rollback did not clear the claim';
    END IF;
    first_command := result.command_id;

    SELECT * INTO STRICT result FROM rover_backend.claim_dispatch(first_mission);
    IF result.outcome IS DISTINCT FROM 'already_claimed'
       OR result.command_id IS DISTINCT FROM first_command THEN
        RAISE EXCEPTION 'retry did not return the original command ID';
    END IF;

    UPDATE rover_backend.missions SET state = 'unknown' WHERE id = first_mission;
    SELECT * INTO STRICT result FROM rover_backend.claim_dispatch(first_mission);
    IF result.outcome IS DISTINCT FROM 'already_claimed'
       OR result.mission_state IS DISTINCT FROM 'unknown'
       OR result.command_id IS DISTINCT FROM first_command THEN
        RAISE EXCEPTION 'unknown command retry lost the original ID or state';
    END IF;

    SELECT * INTO STRICT result FROM rover_backend.claim_dispatch(second_mission);
    IF result.outcome IS DISTINCT FROM 'rover_busy'
       OR result.mission_state IS DISTINCT FROM 'queued' OR result.command_id IS NOT NULL THEN
        RAISE EXCEPTION 'unknown mission did not hold the rover slot';
    END IF;

    IF (SELECT cursor FROM rover_backend.event_head) <> initial_cursor + 1
       OR NOT EXISTS (
           SELECT FROM rover_backend.events
           WHERE cursor = initial_cursor + 1 AND kind = 'mission.dispatching'
             AND entity_id = first_mission
             AND payload = jsonb_build_object(
                 'state', 'dispatching', 'objectId', target,
                 'roverId', 'rover', 'commandId', first_command
             )
       ) THEN
        RAISE EXCEPTION 'expected exactly one dispatch event matching the successful claim';
    END IF;

    UPDATE rover_backend.missions SET state = 'failed' WHERE id = first_mission;
    SELECT * INTO STRICT result FROM rover_backend.claim_dispatch(second_mission);
    IF result.outcome IS DISTINCT FROM 'claimed' OR result.command_id IS NULL
       OR result.command_id = first_command THEN
        RAISE EXCEPTION 'terminal mission did not release its rover slot';
    END IF;
END;
$$;

ROLLBACK;
