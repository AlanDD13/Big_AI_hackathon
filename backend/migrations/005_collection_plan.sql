-- Store route metadata separately from the idempotency comparison body.
BEGIN;

ALTER TABLE rover_backend.missions
    ADD COLUMN collection_plan jsonb
        CHECK (collection_plan IS NULL OR jsonb_typeof(collection_plan) = 'object');

COMMIT;
