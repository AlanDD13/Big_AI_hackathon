"""Store and reload mapping observations in rover_backend.

The wire object id stays cameraId:sessionId:trackId. Postgres keeps its own UUID.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

from backend.evidence import sample_for
from backend.postgres_evidence import PostgresEvidenceStore

if TYPE_CHECKING:
    from psycopg import Connection


class StorageUnavailable(RuntimeError):
    """The database could not store or reload an observation."""


def event_id_for(evidence: Mapping[str, object], vision_event_id: str | None) -> str:
    if vision_event_id:
        return vision_event_id
    return (
        f"mapping:{evidence['cameraId']}:{evidence['sessionId']}:"
        f"{evidence['geometryVersion']}:{evidence['trackId']}:{evidence['frameId']}"
    )


class PostgresObservationLog:
    def __init__(self, connect: Callable[[], Connection]) -> None:
        self._connect = connect
        self._store = PostgresEvidenceStore(connect)

    def save(self, evidence: Mapping[str, object], event_id: str) -> None:
        try:
            self._store.record_observation(
                "mapping",
                event_id,
                sample_for(evidence),
                frame_id=int(evidence["frameId"]),
            )
        except ValueError:
            raise
        except Exception as exc:
            raise StorageUnavailable("Observation storage is unavailable") from exc

    def load(self) -> list[tuple[dict[str, object], str]]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """SELECT object.camera_id, object.session_id, object.track_id,
                              observation.geometry_version, observation.frame_id,
                              observation.event_id, observation.data
                       FROM rover_backend.observations AS observation
                       JOIN rover_backend.objects AS object ON object.id = observation.object_id
                       ORDER BY observation.observed_at, observation.frame_id, observation.event_id"""
                ).fetchall()
        except Exception as exc:
            raise StorageUnavailable("Observation storage is unavailable") from exc
        loaded = []
        for camera_id, session_id, track_id, geometry, frame_id, event_id, data in rows:
            loaded.append((evidence_from_row(
                camera_id, session_id, track_id, geometry, frame_id, data
            ), str(event_id)))
        return loaded


def evidence_from_row(
    camera_id: str,
    session_id: str,
    track_id: str,
    geometry: str | None,
    frame_id: int | None,
    data: Mapping[str, object],
) -> dict[str, object]:
    context = data["context"]
    point = data["point_m"]
    valid = bool(data["position_valid"])
    calibration_valid = bool(data["calibration_valid"])
    if valid:
        reason = None
    elif not data["is_fixed_camera"]:
        reason = "rover_confirmation_only"
    elif not calibration_valid:
        reason = "uncalibrated"
    else:
        reason = "unknown"
    received_at = float(data["received_at"])
    return {
        "cameraId": camera_id,
        "sessionId": session_id,
        "geometryVersion": geometry or str(context["geometry_version"]),
        "frameId": int(frame_id if frame_id is not None else 0),
        "trackId": track_id,
        "mapId": str(context["map_id"]),
        "mapVersion": str(context["map_version"]),
        "calibrationId": str(context["calibration_id"]),
        "calibrationValid": calibration_valid,
        "capturedAt": received_at,
        "receivedAt": received_at,
        "floorPointM": None if point is None else [float(point[0]), float(point[1])],
        "positionErrorM": None if data["position_error_m"] is None else float(data["position_error_m"]),
        "valid": valid,
        "invalidReason": reason,
    }
