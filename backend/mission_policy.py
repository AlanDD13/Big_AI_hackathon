"""Backend policy for admitting a collection candidate."""

from dataclasses import dataclass
from enum import Enum

from backend.stationarity import Stationarity


class Rejection(str, Enum):
    STATIONARITY_REQUIRED = "stationarity_required"
    CLASSIFICATION_REQUIRED = "classification_required"
    CLASSIFICATION_EXCLUDED = "classification_excluded"
    PICKUP_PROFILE_REQUIRED = "pickup_profile_required"


@dataclass(frozen=True)
class Classification:
    status: str
    category: str
    subject_kind: str
    carrying: str
    confidence: str


def collection_rejection(
    stationarity: Stationarity,
    classification: Classification | None,
    *,
    pickup_profile_id: str | None,
) -> Rejection | None:
    """Check evidence and pickup policy before a caller validates the route."""
    if not stationarity.eligible:
        return Rejection.STATIONARITY_REQUIRED
    if classification is None or classification.status != "COMPLETED":
        return Rejection.CLASSIFICATION_REQUIRED
    if (
        classification.category not in {"TRASH", "POTENTIALLY_LOST_PROPERTY"}
        or classification.subject_kind != "INANIMATE"
        or classification.carrying != "NO"
        or classification.confidence not in {"MEDIUM", "HIGH"}
    ):
        return Rejection.CLASSIFICATION_EXCLUDED
    if not pickup_profile_id:
        return Rejection.PICKUP_PROFILE_REQUIRED
    return None
