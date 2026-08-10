"""Value-free patient identity decision states.

This module classifies already-compared identity field statuses. It does not
accept, normalize, compare, or retain patient identity values.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum


class IdentityFieldStatus(str, Enum):
    """Closed outcomes from an identity-field comparison boundary."""

    EXACT = "EXACT"
    MISSING = "MISSING"
    NONMATCHING = "NONMATCHING"
    CONFLICTING = "CONFLICTING"


class IdentityDecisionState(str, Enum):
    """Closed aggregate identity states."""

    IDENTITY_CONFIRMED = "IDENTITY_CONFIRMED"
    IDENTITY_DISCREPANCY = "IDENTITY_DISCREPANCY"
    IDENTITY_INSUFFICIENT = "IDENTITY_INSUFFICIENT"
    IDENTITY_CONFLICTING = "IDENTITY_CONFLICTING"


@dataclass(frozen=True, slots=True)
class IdentityDecision:
    """Immutable, value-free aggregate of identity comparison statuses."""

    state: IdentityDecisionState
    evaluated_field_count: int
    exact_count: int
    missing_count: int
    nonmatching_count: int
    conflicting_count: int
    authoritative_context_present: bool

    def __post_init__(self) -> None:
        counts = (
            self.exact_count,
            self.missing_count,
            self.nonmatching_count,
            self.conflicting_count,
        )
        if not isinstance(self.authoritative_context_present, bool):
            raise TypeError("authoritative_context_present must be a bool")
        if any(isinstance(count, bool) or not isinstance(count, int) for count in counts):
            raise TypeError("identity counts must be integers")
        if any(count < 0 for count in counts):
            raise ValueError("identity counts cannot be negative")
        if self.evaluated_field_count != sum(counts):
            raise ValueError("identity counts must reconcile")


def decide_identity_state(
    *,
    authoritative_context_present: bool,
    field_statuses: Mapping[str, IdentityFieldStatus],
) -> IdentityDecision:
    """Aggregate closed field statuses without receiving identity values."""

    if not isinstance(authoritative_context_present, bool):
        raise TypeError("authoritative_context_present must be a bool")
    if not isinstance(field_statuses, Mapping):
        raise TypeError("field_statuses must be a mapping")

    counts = {status: 0 for status in IdentityFieldStatus}
    for field_name, status in field_statuses.items():
        if not isinstance(field_name, str) or not field_name:
            raise TypeError("identity field names must be non-empty strings")
        if not isinstance(status, IdentityFieldStatus):
            raise TypeError("identity field statuses must use IdentityFieldStatus")
        counts[status] += 1

    evaluated_field_count = sum(counts.values())
    if not authoritative_context_present:
        state = IdentityDecisionState.IDENTITY_INSUFFICIENT
    elif counts[IdentityFieldStatus.CONFLICTING]:
        state = IdentityDecisionState.IDENTITY_CONFLICTING
    elif counts[IdentityFieldStatus.NONMATCHING]:
        state = IdentityDecisionState.IDENTITY_DISCREPANCY
    elif not evaluated_field_count or counts[IdentityFieldStatus.MISSING]:
        state = IdentityDecisionState.IDENTITY_INSUFFICIENT
    else:
        state = IdentityDecisionState.IDENTITY_CONFIRMED

    return IdentityDecision(
        state=state,
        evaluated_field_count=evaluated_field_count,
        exact_count=counts[IdentityFieldStatus.EXACT],
        missing_count=counts[IdentityFieldStatus.MISSING],
        nonmatching_count=counts[IdentityFieldStatus.NONMATCHING],
        conflicting_count=counts[IdentityFieldStatus.CONFLICTING],
        authoritative_context_present=authoritative_context_present,
    )
