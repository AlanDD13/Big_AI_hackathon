-- Internal reservation and enqueue operation. This does not dispatch the rover.
BEGIN;

CREATE FUNCTION rover_backend.enqueue_mission(
    target_object_id uuid,
    assigned_rover_id text,
    authenticated_scope text,
    request_key text,
    normalized_request jsonb
)
RETURNS TABLE (outcome text, mission_id uuid, mission_state text)
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    stored rover_backend.missions%ROWTYPE;
    conflicting_constraint text;
BEGIN
    SELECT mission.* INTO stored
    FROM rover_backend.missions AS mission
    WHERE mission.request_scope = authenticated_scope
      AND mission.idempotency_key = request_key;

    IF NOT FOUND THEN
        BEGIN
            INSERT INTO rover_backend.missions AS mission
                (id, object_id, rover_id, request_scope, idempotency_key, request_data)
            VALUES
                (gen_random_uuid(), target_object_id, assigned_rover_id,
                 authenticated_scope, request_key, normalized_request)
            ON CONFLICT (request_scope, idempotency_key) DO NOTHING
            RETURNING mission.* INTO stored;
        EXCEPTION WHEN unique_violation THEN
            GET STACKED DIAGNOSTICS conflicting_constraint = CONSTRAINT_NAME;
            IF conflicting_constraint IS DISTINCT FROM 'one_live_mission_per_object' THEN
                RAISE;
            END IF;
        END;

        IF stored.id IS NOT NULL THEN
            PERFORM rover_backend.append_event(
                'mission.queued', stored.id,
                jsonb_build_object(
                    'state', stored.state,
                    'objectId', stored.object_id,
                    'roverId', stored.rover_id
                )
            );
            RETURN QUERY SELECT 'created'::text, stored.id, stored.state;
            RETURN;
        END IF;

        -- At READ COMMITTED this statement sees a concurrent key winner even
        -- if the INSERT first encountered the object reservation index.
        SELECT mission.* INTO stored
        FROM rover_backend.missions AS mission
        WHERE mission.request_scope = authenticated_scope
          AND mission.idempotency_key = request_key;
    END IF;

    IF stored.id IS NULL THEN
        RETURN QUERY SELECT 'object_reserved'::text, NULL::uuid, NULL::text;
    ELSIF stored.object_id IS DISTINCT FROM target_object_id
       OR stored.request_data IS DISTINCT FROM normalized_request THEN
        RETURN QUERY SELECT 'key_conflict'::text, NULL::uuid, NULL::text;
    ELSE
        -- Assignment is server-side. Replaying an identical operator request
        -- returns the existing mission regardless of a newly selected rover.
        RETURN QUERY SELECT 'replayed'::text, stored.id, stored.state;
    END IF;
END;
$$;

COMMENT ON FUNCTION rover_backend.enqueue_mission(uuid, text, text, text, jsonb) IS
    'Internal storage operation. Check existing request keys before running policy on a new request. Validate and lock the object in the caller transaction for a new mission. Retry serialization failures as whole transactions.';

REVOKE ALL ON FUNCTION rover_backend.enqueue_mission(uuid, text, text, text, jsonb) FROM PUBLIC;

COMMIT;
