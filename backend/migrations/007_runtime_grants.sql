-- Grant the dedicated runtime role access to rover_backend only.
-- The Supabase project owner creates this role. Local databases without it skip the grants.
BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rover_backend_runtime') THEN
        GRANT USAGE ON SCHEMA rover_backend TO rover_backend_runtime;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA rover_backend
            TO rover_backend_runtime;
        GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA rover_backend TO rover_backend_runtime;
    END IF;
END
$$;

COMMIT;
