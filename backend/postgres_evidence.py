"""Persist normalized mapping evidence and load it for collection admission."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict
import json
from math import isfinite
from typing import TYPE_CHECKING

from backend.collection import CollectionEvidence
from backend.mission_policy import Classification
from backend.stationarity import MetricSample, TrackContext

if TYPE_CHECKING:
    from psycopg import Connection


def normalize_classification(data: Mapping[str, object]) -> dict[str, object]:
    """Read both current flat classifications and the original Vision envelope."""
    if "classification" not in data:
        return dict(data)
    nested = data.get("classification")
    normalized = dict(nested) if isinstance(nested, dict) else {}
    normalized["provider"] = data.get("classificationProvider")
    return normalized


class PostgresEvidenceStore:
    def __init__(self, connect: Callable[[], Connection]) -> None:
        self._connect = connect

    def record_observation(
        self, producer: str, event_id: str, sample: MetricSample, *, frame_id: int,
        wire_evidence: Mapping[str, object] | None = None,
        source: Mapping[str, object] | None = None,
    ) -> str:
        """Record a trusted, normalized metric sample or replay its exact frame."""
        if type(frame_id) is not int or frame_id < 0 or not isfinite(sample.received_at):
            raise ValueError("Observation requires a nonnegative frame and finite receipt time")
        context = sample.context
        data = json.dumps(asdict(sample), allow_nan=False)
        with self._connect() as connection, connection.transaction():
            connection.execute(
                """INSERT INTO rover_backend.objects (id, camera_id, session_id, track_id, state)
                   VALUES (gen_random_uuid(), %s, %s, %s, '{}')
                   ON CONFLICT (camera_id, session_id, track_id) DO NOTHING""",
                (context.camera_id, context.session_id, context.track_id),
            )
            object_id, = connection.execute(
                """SELECT id FROM rover_backend.objects
                   WHERE camera_id = %s AND session_id = %s AND track_id = %s
                   FOR UPDATE""",
                (context.camera_id, context.session_id, context.track_id),
            ).fetchone()
            payload = {"frameId": frame_id, "sample": asdict(sample)}
            if wire_evidence is not None:
                payload["wireEvidence"] = dict(wire_evidence)
                payload["source"] = dict(source or {})
            envelope = json.dumps(payload, allow_nan=False)
            wire = json.dumps(wire_evidence, allow_nan=False)
            inserted = connection.execute(
                """INSERT INTO rover_backend.ingest_events (producer, event_id, payload)
                   VALUES (%s, %s, %s::jsonb)
                   ON CONFLICT DO NOTHING RETURNING event_id""",
                (producer, event_id, envelope),
            ).fetchone()
            if inserted is None:
                matches, = connection.execute(
                    """SELECT payload - 'source' - 'wireEvidence' =
                                  %s::jsonb - 'source' - 'wireEvidence'
                              AND (payload -> 'wireEvidence' IS NULL
                                   OR payload -> 'wireEvidence' = %s::jsonb)
                       FROM rover_backend.ingest_events
                       WHERE producer = %s AND event_id = %s""",
                    (envelope, wire, producer, event_id),
                ).fetchone()
                if not matches:
                    raise ValueError("Observation event ID was reused with different evidence")
                return str(object_id)

            frame = connection.execute(
                """SELECT observation.data = %s::jsonb
                          AND (ingest.payload -> 'wireEvidence' IS NULL
                               OR ingest.payload -> 'wireEvidence' = %s::jsonb)
                   FROM rover_backend.observations AS observation
                   JOIN rover_backend.ingest_events AS ingest USING (producer, event_id)
                   WHERE object_id = %s AND geometry_version = %s AND frame_id = %s""",
                (data, wire, object_id, context.geometry_version, frame_id),
            ).fetchone()
            if frame is not None:
                if not frame[0]:
                    raise ValueError("Observation frame was reused with different evidence")
                return str(object_id)

            obsolete, = connection.execute(
                """SELECT EXISTS (
                       SELECT FROM rover_backend.observations WHERE object_id = %s
                       AND (observed_at > to_timestamp(%s)
                            OR (geometry_version = %s AND frame_id > %s))
                   )""",
                (object_id, sample.received_at, context.geometry_version, frame_id),
            ).fetchone()
            if obsolete:
                raise ValueError("Observation is older than stored evidence")

            connection.execute(
                """INSERT INTO rover_backend.observations
                       (producer, event_id, object_id, observed_at, data, geometry_version, frame_id)
                   VALUES (%s, %s, %s, to_timestamp(%s), %s::jsonb, %s, %s)""",
                (producer, event_id, object_id, sample.received_at, data, context.geometry_version, frame_id),
            )
            revision, = connection.execute(
                """UPDATE rover_backend.objects SET revision = revision + 1
                   WHERE id = %s RETURNING revision""",
                (object_id,),
            ).fetchone()
            connection.execute(
                """SELECT rover_backend.append_event(
                       'object.observation_recorded', %s,
                       jsonb_build_object('revision', %s::bigint, 'evidence', %s::jsonb)
                   )""",
                (object_id, revision, envelope),
            )
            return str(object_id)

    def load(
        self,
        connection: Connection,
        object_id: str,
        *,
        require_classification_provider: bool = False,
    ) -> CollectionEvidence | None:
        # One query gives unlocked planning a consistent view as well.
        row = connection.execute(
            """SELECT object.revision, object.state ->> 'pickupProfileId',
                      classification.status, classification.data,
                      COALESCE((
                          SELECT jsonb_agg(data ORDER BY observed_at, frame_id, event_id)
                          FROM rover_backend.observations WHERE object_id = object.id
                      ), '[]'::jsonb),
                      object.state ->> 'status', object.state ->> 'state'
               FROM rover_backend.objects AS object
               LEFT JOIN rover_backend.classifications AS classification
                   ON classification.object_id = object.id
               WHERE object.id = %s""",
            (object_id,),
        ).fetchone()
        if row is None:
            return None

        revision, profile, status, classification, samples, vision_status, vision_state = row
        if vision_status in {"LOST", "EXCLUDED"} or vision_state in {"LOST", "EXCLUDED"}:
            # Historical metric samples remain stored, but they cannot revive
            # a currently revoked Vision track for planning or reservation.
            samples = []
        typed_classification = None
        if status is not None:
            classification = normalize_classification(classification)
        if status is not None and (
            not require_classification_provider or classification.get("provider") in ("grok", "fixture")
        ):
            typed_classification = Classification(
                status.upper(),
                classification.get("category", ""),
                classification.get("subject_kind", ""),
                classification.get("carrying", ""),
                classification.get("confidence", ""),
                classification.get("provider"),
                classification.get("description"),
                classification.get("reason"),
            )
        typed_samples = []
        for sample in samples:
            context = TrackContext(**sample["context"])
            point = sample["point_m"]
            typed_samples.append(
                MetricSample(
                    context, sample["received_at"], tuple(point) if point is not None else None,
                    sample["position_error_m"], sample["is_fixed_camera"],
                    sample["position_valid"], sample["calibration_valid"],
                )
            )
        return CollectionEvidence(revision, tuple(typed_samples), typed_classification, profile)
