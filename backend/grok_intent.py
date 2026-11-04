"""Decode the structured action proposed by the Grok adapter."""

from collections.abc import Mapping
from dataclasses import dataclass


class InvalidIntent(ValueError):
    """A proposed action falls outside the backend's supported surface."""


@dataclass(frozen=True)
class InspectObjects:
    pass


@dataclass(frozen=True)
class ExplainAlert:
    alert_id: str


@dataclass(frozen=True)
class ProposeCollection:
    object_id: str
    destination_zone_id: str


Intent = InspectObjects | ExplainAlert | ProposeCollection


def _read_ids(arguments: Mapping[str, object], *names: str) -> list[str]:
    if set(arguments) != set(names):
        raise InvalidIntent("Unexpected or missing action arguments")

    ids = []
    for name in names:
        value = arguments[name]
        if not isinstance(value, str) or not value or value.strip() != value:
            raise InvalidIntent(f"{name} must be a nonempty identifier")
        ids.append(value)
    return ids


def decode_intent(action: str, arguments: Mapping[str, object]) -> Intent:
    """Parse model arguments. Callers must resolve IDs and apply mission policy."""
    if not isinstance(arguments, Mapping):
        raise InvalidIntent("Action arguments must be an object")

    if action == "inspect_objects":
        _read_ids(arguments)
        return InspectObjects()
    if action == "explain_alert":
        (alert_id,) = _read_ids(arguments, "alertId")
        return ExplainAlert(alert_id)
    if action == "propose_collect":
        object_id, destination_zone_id = _read_ids(
            arguments, "objectId", "destinationZoneId"
        )
        return ProposeCollection(object_id, destination_zone_id)

    raise InvalidIntent("Unsupported action")
