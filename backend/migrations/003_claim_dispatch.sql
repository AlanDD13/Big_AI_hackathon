-- Persist dispatch intent before any bridge call. No bridge access occurs here.
BEGIN;

CREATE FUNCTION rover_backend.claim_dispatch(target_mission_id uuid)
RETURNS TABLE (outcome text, mission_id uuid, mission_state text, command_id uuid)
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    stored rover_backend.missions%ROWTYPE;
    conflicting_constraint text;
BEGIN
    SELECT mission.* INTO stored
    FROM rover_backend.missions AS mission
    WHERE mission.id = target_mission_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RETURN QUERY SELECT 'not_found'::text, NULL::uuid, NULL::text, NULL::uuid;
        RETURN;
    END IF;
    IF stored.command_id IS NOT NULL THEN
        RETURN QUERY SELECT 'already_claimed'::text, stored.id, stored.state, stored.command_id;
        RETURN;
    END IF;
    IF stored.state <> 'queued' THEN
        RETURN QUERY SELECT 'not_queued'::text, stored.id, stored.state, NULL::uuid;
        RETURN;
    END IF;

    BEGIN
        UPDATE rover_backend.missions AS mission
        SET state = 'dispatching',
            command_id = gen_random_uuid(),
            updated_at = clock_timestamp()
        WHERE mission.id = target_mission_id
        RETURNING mission.* INTO stored;
    EXCEPTION WHEN unique_violation THEN
        GET STACKED DIAGNOSTICS conflicting_constraint = CONSTRAINT_NAME;
        IF conflicting_constraint IS DISTINCT FROM 'one_active_mission_per_rover' THEN
            RAISE;
        END IF;
        RETURN QUERY SELECT 'rover_busy'::text, target_mission_id, 'queued'::text, NULL::uuid;
        RETURN;
    END;

    PERFORM rover_backend.append_event(
        'mission.dispatching', stored.id,
        jsonb_build_object(
            'state', stored.state,
            'objectId', stored.object_id,
            'roverId', stored.rover_id,
            'commandId', stored.command_id
        )
    );
    RETURN QUERY SELECT 'claimed'::text, stored.id, stored.state, stored.command_id;
END;
$$;

COMMENT ON FUNCTION rover_backend.claim_dispatch(uuid) IS
    'Validate object and route under caller-held locks. Only a newly claimed outcome with a confirmed commit may initiate a bridge send. On retry, reconcile the original command ID without resending.';

REVOKE ALL ON FUNCTION rover_backend.claim_dispatch(uuid) FROM PUBLIC;

COMMIT;
