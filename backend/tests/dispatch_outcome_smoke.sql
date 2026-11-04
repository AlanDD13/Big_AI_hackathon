-- Run after migrations 001 through 004 in a disposable database.
BEGIN;

DO $$
DECLARE
    target uuid := '00000000-0000-0000-0000-000000000031';
    other_target uuid := '00000000-0000-0000-0000-000000000032';
    first_mission uuid;
    second_mission uuid;
    original_command uuid;
    initial_cursor bigint;
    result record;
BEGIN
    INSERT INTO rover_backend.objects (id, camera_id, session_id, track_id, state)
    VALUES (target, 'cam', 'session', 'track-1', '{}'),
           (other_target, 'cam', 'session', 'track-2', '{}');
    SELECT mission_id INTO STRICT first_mission FROM rover_backend.enqueue_mission(
        target, 'rover', 'operator', 'intent-1', '{}'
    );
    SELECT mission_id INTO STRICT second_mission FROM rover_backend.enqueue_mission(
        other_target, 'rover', 'operator', 'intent-2', '{}'
    );
    SELECT command_id INTO STRICT original_command
    FROM rover_backend.claim_dispatch(first_mission) WHERE outcome = 'claimed';
    SELECT cursor INTO STRICT initial_cursor FROM rover_backend.event_head;

    SELECT * INTO STRICT result FROM rover_backend.record_dispatch_outcome(
        target, original_command, 1, 'running'
    );
    IF result.outcome IS DISTINCT FROM 'not_found' THEN
        RAISE EXCEPTION 'missing mission matched another command';
    END IF;

    SELECT * INTO STRICT result FROM rover_backend.record_dispatch_outcome(
        first_mission, other_target, 1, 'succeeded'
    );
    IF result.outcome IS DISTINCT FROM 'command_mismatch'
       OR result.status_revision IS DISTINCT FROM 1 THEN
        RAISE EXCEPTION 'mismatched command changed the claimed mission';
    END IF;

    BEGIN
        SELECT * INTO STRICT result FROM rover_backend.record_dispatch_outcome(
            first_mission, original_command, 1, 'running'
        );
        IF result.outcome IS DISTINCT FROM 'recorded'
           OR result.status_revision IS DISTINCT FROM 2 THEN
            RAISE EXCEPTION 'valid observation was not recorded';
        END IF;
        RAISE SQLSTATE 'Z0001' USING MESSAGE = 'test rollback';
    EXCEPTION WHEN SQLSTATE 'Z0001' THEN
        NULL;
    END;

    SELECT * INTO STRICT result FROM rover_backend.record_dispatch_outcome(
        first_mission, original_command, 1, 'unknown', 'bridge_timeout'
    );
    IF result.outcome IS DISTINCT FROM 'recorded'
       OR result.mission_state IS DISTINCT FROM 'unknown'
       OR result.status_revision IS DISTINCT FROM 2 THEN
        RAISE EXCEPTION 'rollback did not restore revision or unknown was not recorded';
    END IF;
    IF NOT EXISTS (
        SELECT FROM rover_backend.claim_dispatch(second_mission) WHERE outcome = 'rover_busy'
    ) THEN
        RAISE EXCEPTION 'unknown outcome did not retain the rover slot';
    END IF;

    SELECT * INTO STRICT result FROM rover_backend.record_dispatch_outcome(
        first_mission, original_command, 2, 'unknown', 'bridge_timeout'
    );
    IF result.outcome IS DISTINCT FROM 'unchanged'
       OR result.status_revision IS DISTINCT FROM 2 THEN
        RAISE EXCEPTION 'duplicate report created another revision';
    END IF;

    SELECT * INTO STRICT result FROM rover_backend.record_dispatch_outcome(
        first_mission, original_command, 2, 'running'
    );
    IF result.outcome IS DISTINCT FROM 'recorded'
       OR result.status_revision IS DISTINCT FROM 3
       OR EXISTS (
           SELECT FROM rover_backend.missions WHERE id = first_mission AND status_data ? 'reason'
       ) THEN
        RAISE EXCEPTION 'reconciliation did not update status and clear the old reason';
    END IF;

    SELECT * INTO STRICT result FROM rover_backend.record_dispatch_outcome(
        first_mission, original_command, 3, 'dispatching'
    );
    IF result.outcome IS DISTINCT FROM 'invalid_transition'
       OR result.status_revision IS DISTINCT FROM 3 THEN
        RAISE EXCEPTION 'running mission regressed on an old acceptance';
    END IF;

    SELECT * INTO STRICT result FROM rover_backend.record_dispatch_outcome(
        first_mission, original_command, 3, 'unknown', 'bridge_timeout'
    );
    IF result.outcome IS DISTINCT FROM 'recorded'
       OR result.status_revision IS DISTINCT FROM 4 THEN
        RAISE EXCEPTION 'second unknown status did not advance revision';
    END IF;

    SELECT * INTO STRICT result FROM rover_backend.record_dispatch_outcome(
        first_mission, original_command, 2, 'succeeded'
    );
    IF result.outcome IS DISTINCT FROM 'stale_revision'
       OR result.mission_state IS DISTINCT FROM 'unknown'
       OR result.status_revision IS DISTINCT FROM 4 THEN
        RAISE EXCEPTION 'an old observation advanced the same repeated state';
    END IF;

    SELECT * INTO STRICT result FROM rover_backend.record_dispatch_outcome(
        first_mission, original_command, 4, 'succeeded'
    );
    IF result.outcome IS DISTINCT FROM 'recorded'
       OR result.status_revision IS DISTINCT FROM 5 THEN
        RAISE EXCEPTION 'fresh terminal result did not settle the mission';
    END IF;

    SELECT * INTO STRICT result FROM rover_backend.record_dispatch_outcome(
        first_mission, original_command, 5, 'failed'
    );
    IF result.outcome IS DISTINCT FROM 'terminal'
       OR result.mission_state IS DISTINCT FROM 'succeeded'
       OR result.status_revision IS DISTINCT FROM 5 THEN
        RAISE EXCEPTION 'terminal state was overwritten';
    END IF;

    IF (SELECT cursor FROM rover_backend.event_head) <> initial_cursor + 4
       OR NOT EXISTS (
           SELECT FROM rover_backend.events
           WHERE cursor = initial_cursor + 1 AND kind = 'mission.dispatch_outcome'
             AND entity_id = first_mission AND payload = jsonb_build_object(
                 'state', 'unknown', 'objectId', target, 'roverId', 'rover',
                 'commandId', original_command, 'statusRevision', 2, 'reason', 'bridge_timeout'
             )
       ) THEN
        RAISE EXCEPTION 'status events did not match the four committed changes';
    END IF;

    IF NOT EXISTS (
        SELECT FROM rover_backend.claim_dispatch(second_mission) WHERE outcome = 'claimed'
    ) THEN
        RAISE EXCEPTION 'settled mission did not release the rover slot';
    END IF;
END;
$$;

ROLLBACK;
