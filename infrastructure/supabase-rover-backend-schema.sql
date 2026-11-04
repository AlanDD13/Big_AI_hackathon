-- ============================================================================
-- Hackathon Dedicated Schema Bootstrap (`rover_backend`)
-- Target Project: AlanDD13's Project (`buojgmoumyzqxmerckjm`, eu-west-1)
-- Purpose: Isolate all hackathon tables, views, sequences, and routines inside
--          schema `rover_backend` so the existing `public` schema (used by
--          adaptive-python-mentor) is never modified or polluted.
-- Run once in Supabase SQL Editor:
--   https://supabase.com/dashboard/project/buojgmoumyzqxmerckjm/sql/new
-- ============================================================================

BEGIN;

CREATE SCHEMA IF NOT EXISTS rover_backend;

-- Grant schema usage to PostgREST roles; keep write/all access scoped to service_role
GRANT USAGE ON SCHEMA rover_backend TO anon, authenticated, service_role;
GRANT ALL ON SCHEMA rover_backend TO postgres, service_role;

GRANT ALL ON ALL TABLES IN SCHEMA rover_backend TO service_role;
GRANT ALL ON ALL SEQUENCES IN SCHEMA rover_backend TO service_role;
GRANT ALL ON ALL ROUTINES IN SCHEMA rover_backend TO service_role;

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA rover_backend
  GRANT ALL ON TABLES TO service_role;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA rover_backend
  GRANT ALL ON SEQUENCES TO service_role;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA rover_backend
  GRANT ALL ON ROUTINES TO service_role;

-- Expose `rover_backend` alongside `public, graphql_public` in PostgREST Data API
ALTER ROLE authenticator SET pgrst.db_schemas = 'public, graphql_public, rover_backend';
NOTIFY pgrst, 'reload config';
NOTIFY pgrst, 'reload schema';

COMMIT;
