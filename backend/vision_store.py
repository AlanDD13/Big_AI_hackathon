"""Persist vision ingest in rover_backend and reload it after a restart.

The dashboard object id stays cameraId:sessionId:trackId. Postgres keeps its UUID.
A missing database is reported without the connection string.
"""

from __future__ import annotations

from collections.abc import Callable
import json
from typing import TYPE_CHECKING

from backend.postgres_projection import StorageUnavailable
from backend.vision_ingest import ParsedVisionEvent

if TYPE_CHECKING:
    from psycopg import Connection


_CLASSIFICATION_STATUS = {
    "PENDING": "pending",
    "COMPLETED": "completed",
    "UNAVAILABLE": "unavailable",
}


class PostgresVisionLog:
    def __init__(self, connect: Callable[[], Connection]) -> None:
        self._connect = connect

    def save(self, event: ParsedVisionEvent, body: dict[str, object] | None) -> None:
        public = None if body is None else {
            key: value for key, value in body.items() if not str(key).startswith("_")
        }
        payload = json.dumps(
            {"canonical": json.loads(event.canonical), "object": public},
            allow_nan=False,
        )
        try:
            with self._connect() as connection, connection.transaction():
                inserted = connection.execute(
                    """INSERT INTO rover_backend.ingest_events (producer, event_id, payload)
                       VALUES ('vision', %s, %s::jsonb)
                       ON CONFLICT DO NOTHING
                       RETURNING event_id""",
                    (event.event_id, payload),
                ).fetchone()
                if inserted is None:
                    stored, = connection.execute(
                        """SELECT payload -> 'canonical'
                           FROM rover_backend.ingest_events
                           WHERE producer = 'vision' AND event_id = %s""",
                        (event.event_id,),
                    ).fetchone()
                    if json.dumps(stored, sort_keys=True, separators=(",", ":")) != event.canonical:
                        raise ValueError("Vision event ID was reused with a different body")
                    return
                if public is not None:
                    camera_id = public["cameraId"]
                    session_id = public["sessionId"]
                    track_id = public["trackId"]
                    connection.execute(
                        """INSERT INTO rover_backend.objects
                               (id, camera_id, session_id, track_id, state)
                           VALUES (gen_random_uuid(), %s, %s, %s, %s::jsonb)
                           ON CONFLICT (camera_id, session_id, track_id)
                           DO UPDATE SET state = rover_backend.objects.state || EXCLUDED.state,
                                         revision = rover_backend.objects.revision + 1""",
                        (camera_id, session_id, track_id, json.dumps(public, allow_nan=False)),
                    )
                    status = _CLASSIFICATION_STATUS.get(str(public.get("classificationStatus")))
                    if status is not None:
                        connection.execute(
                            """INSERT INTO rover_backend.classifications (object_id, status, data)
                               SELECT id, %s, %s::jsonb FROM rover_backend.objects
                               WHERE camera_id = %s AND session_id = %s AND track_id = %s
                               ON CONFLICT (object_id) DO UPDATE
                               SET status = EXCLUDED.status,
                                   data = EXCLUDED.data,
                                   updated_at = clock_timestamp()""",
                            (
                                status,
                                json.dumps(
                                    {
                                        **(public.get("classification") or {}),
                                        "provider": public.get("classificationProvider"),
                                    },
                                    allow_nan=False,
                                ),
                                camera_id,
                                session_id,
                                track_id,
                            ),
                        )
                if event.alert_kind is not None:
                    connection.execute(
                        """INSERT INTO rover_backend.alerts
                               (id, object_id, transition_key, kind, data)
                           VALUES (
                               gen_random_uuid(),
                               (SELECT id FROM rover_backend.objects
                                WHERE camera_id = %s AND session_id = %s AND track_id = %s),
                               %s, %s, %s::jsonb)
                           ON CONFLICT (transition_key) DO NOTHING""",
                        (
                            None if public is None else public.get("cameraId"),
                            None if public is None else public.get("sessionId"),
                            None if public is None else public.get("trackId"),
                            event.event_id,
                            event.alert_kind,
                            payload,
                        ),
                    )
        except ValueError:
            raise
        except Exception as exc:
            raise StorageUnavailable("Observation storage is unavailable") from exc

    def load(self) -> list[dict[str, object]]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """SELECT payload -> 'canonical'
                       FROM rover_backend.ingest_events
                       WHERE producer = 'vision'
                       ORDER BY received_at, event_id"""
                ).fetchall()
        except Exception as exc:
            raise StorageUnavailable("Observation storage is unavailable") from exc
        return [row[0] for row in rows]
