-- Preserve the frame identity used to deduplicate metric observations.
BEGIN;

ALTER TABLE rover_backend.observations
    ADD COLUMN geometry_version text,
    ADD COLUMN frame_id bigint CHECK (frame_id >= 0),
    ADD CHECK ((geometry_version IS NULL) = (frame_id IS NULL));

CREATE UNIQUE INDEX observations_by_frame
    ON rover_backend.observations (object_id, geometry_version, frame_id)
    WHERE frame_id IS NOT NULL;

COMMIT;
