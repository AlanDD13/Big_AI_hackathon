-- ============================================================================
-- Hackathon Dedicated Schema Bootstrap (`rover_backend`)
-- Target Project: AlanDD13's Project (`buojgmoumyzqxmerckjm`, eu-west-1)
-- Purpose: Isolate all hackathon tables, views, sequences, and routines inside
--          schema `rover_backend` for direct Postgres connections via
--          `BACKEND_MIGRATION_DATABASE_URL` (`rover_backend_migrator`) and
--          `BACKEND_DATABASE_URL` (`rover_backend_runtime`) so the existing
--          `public` schema (used by adaptive-python-mentor) is never modified
--          or polluted, and browser/peer modules never query Supabase directly.
-- Run once in Supabase SQL Editor:
--   https://supabase.com/dashboard/project/buojgmoumyzqxmerckjm/sql/new
-- ============================================================================

BEGIN;

CREATE SCHEMA IF NOT EXISTS rover_backend;

-- Browser/dashboard and peer modules must never query Supabase directly.
-- Keep `rover_backend` strictly off PostgREST/Data API roles.
REVOKE ALL ON SCHEMA rover_backend FROM PUBLIC, anon, authenticated;

-- Dedicated least-privilege roles for P3 (`zekihxh`) direct Postgres connections:
--   1. `rover_backend_migrator` (`BACKEND_MIGRATION_DATABASE_URL`): DDL/migrations in `rover_backend`
--   2. `rover_backend_runtime`  (`BACKEND_DATABASE_URL`): DML-only runtime access in `rover_backend`
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rover_backend_migrator') THEN
    CREATE ROLE rover_backend_migrator NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rover_backend_runtime') THEN
    CREATE ROLE rover_backend_runtime NOLOGIN;
  END IF;
END
$$;

REVOKE ALL ON SCHEMA public FROM rover_backend_migrator, rover_backend_runtime;

GRANT USAGE, CREATE ON SCHEMA rover_backend TO rover_backend_migrator;
GRANT USAGE ON SCHEMA rover_backend TO rover_backend_runtime;

GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA rover_backend TO rover_backend_migrator;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA rover_backend TO rover_backend_migrator;
GRANT ALL PRIVILEGES ON ALL ROUTINES IN SCHEMA rover_backend TO rover_backend_migrator;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA rover_backend TO rover_backend_runtime;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA rover_backend TO rover_backend_runtime;
GRANT EXECUTE ON ALL ROUTINES IN SCHEMA rover_backend TO rover_backend_runtime;

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA rover_backend
  GRANT ALL PRIVILEGES ON TABLES TO rover_backend_migrator;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA rover_backend
  GRANT ALL PRIVILEGES ON SEQUENCES TO rover_backend_migrator;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA rover_backend
  GRANT ALL PRIVILEGES ON ROUTINES TO rover_backend_migrator;

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA rover_backend
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO rover_backend_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA rover_backend
  GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO rover_backend_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA rover_backend
  GRANT EXECUTE ON ROUTINES TO rover_backend_runtime;

ALTER DEFAULT PRIVILEGES FOR ROLE rover_backend_migrator IN SCHEMA rover_backend
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO rover_backend_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE rover_backend_migrator IN SCHEMA rover_backend
  GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO rover_backend_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE rover_backend_migrator IN SCHEMA rover_backend
  GRANT EXECUTE ON ROUTINES TO rover_backend_runtime;

ALTER ROLE rover_backend_migrator SET search_path = rover_backend;
ALTER ROLE rover_backend_runtime SET search_path = rover_backend;

COMMIT;
