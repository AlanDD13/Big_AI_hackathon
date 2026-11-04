-- Persist an outcome only for the claimed command and the observed revision.
BEGIN;

ALTER TABLE rover_backend.missions
    ADD COLUMN status_revision bigint NOT NULL DEFAULT 0 CHECK (status_revision >= 0);

CREATE FUNCTION rover_backend.track_mission_status_revision()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    NEW.status_revision := OLD.status_revision;
    IF NEW.state IS DISTINCT FROM OLD.state
       OR NEW.command_id IS DISTINCT FROM OLD.command_id
       OR NEW.status_data IS DISTINCT FROM OLD.status_data THEN
        NEW.status_revision := OLD.status_revision + 1;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER track_mission_status_revision
BEFORE UPDATE ON rover_backend.missions
FOR EACH ROW EXECUTE FUNCTION rover_backend.track_mission_status_revision();

CREATE FUNCTION rover_backend.record_dispatch_outcome(
    target_mission_id uuid,
    target_command_id uuid,
    expected_status_revision bigint,
    reported_state text,
    reported_reason text DEFAULT NULL
)
RETURNS TABLE (outcome text, mission_state text, status_revision bigint)
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    stored rover_backend.missions%ROWTYPE;
    next_status_data jsonb;
BEGIN
    SELECT mission.* INTO stored
    FROM rover_backend.missions AS mission
    WHERE mission.id = target_mission_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RETURN QUERY SELECT 'not_found'::text, NULL::text, NULL::bigint;
        RETURN;
    END IF;
    IF target_command_id IS NULL OR stored.command_id IS DISTINCT FROM target_command_id THEN
        RETURN QUERY SELECT 'command_mismatch'::text, stored.state, stored.status_revision;
        RETURN;
    END IF;
    IF stored.status_revision IS DISTINCT FROM expected_status_revision THEN
        RETURN QUERY SELECT 'stale_revision'::text, stored.state, stored.status_revision;
        RETURN;
    END IF;
    IF stored.state IN ('succeeded', 'failed', 'stopped', 'cancelled') THEN
        RETURN QUERY SELECT 'terminal'::text, stored.state, stored.status_revision;
        RETURN;
    END IF;
    IF stored.state NOT IN ('dispatching', 'running', 'holding', 'unknown')
       OR reported_state IS NULL
       OR reported_state NOT IN ('dispatching', 'running', 'holding', 'unknown',
                                 'succeeded', 'failed', 'stopped', 'cancelled')
       OR (reported_state = 'dispatching' AND stored.state IN ('running', 'holding')) THEN
        RETURN QUERY SELECT 'invalid_transition'::text, stored.state, stored.status_revision;
        RETURN;
    END IF;

    IF reported_reason IS NULL THEN
        next_status_data := stored.status_data - 'reason';
    ELSE
        next_status_data := stored.status_data || jsonb_build_object('reason', reported_reason);
    END IF;
    IF stored.state = reported_state AND stored.status_data = next_status_data THEN
        RETURN QUERY SELECT 'unchanged'::text, stored.state, stored.status_revision;
        RETURN;
    END IF;

    UPDATE rover_backend.missions AS mission
    SET state = reported_state,
        status_data = next_status_data,
        updated_at = clock_timestamp()
    WHERE mission.id = target_mission_id
    RETURNING mission.* INTO stored;

    PERFORM rover_backend.append_event(
        'mission.dispatch_outcome', stored.id,
        jsonb_build_object(
            'state', stored.state,
            'objectId', stored.object_id,
            'roverId', stored.rover_id,
            'commandId', stored.command_id,
            'statusRevision', stored.status_revision,
            'reason', reported_reason
        )
    );
    RETURN QUERY SELECT 'recorded'::text, stored.state, stored.status_revision;
END;
$$;

REVOKE ALL ON FUNCTION rover_backend.track_mission_status_revision() FROM PUBLIC;
REVOKE ALL ON FUNCTION rover_backend.record_dispatch_outcome(uuid, uuid, bigint, text, text) FROM PUBLIC;

COMMIT;
