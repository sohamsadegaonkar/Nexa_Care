"""Versioned clinical-governance policy for emergency access."""

from __future__ import annotations

import re
from enum import Enum


BREAK_GLASS_POLICY_VERSION = "2026-07-v1"
BREAK_GLASS_REASON_CODE_VERSION = "v1"
MAX_BREAK_GLASS_JUSTIFICATION_LENGTH = 500


class BreakGlassReasonCode(str, Enum):
    UNCONSCIOUS_PATIENT = "UNCONSCIOUS_PATIENT"
    LIFE_THREATENING_EMERGENCY = "LIFE_THREATENING_EMERGENCY"
    PATIENT_UNABLE_TO_CONSENT = "PATIENT_UNABLE_TO_CONSENT"


# These categories intentionally match downstream consent-gate purpose names.
# They are an upper bound: a client may request fewer but never more.
BREAK_GLASS_SCOPE_BY_REASON: dict[BreakGlassReasonCode, frozenset[str]] = {
    BreakGlassReasonCode.UNCONSCIOUS_PATIENT: frozenset(
        {"clinical.allergies", "clinical.active_medications", "clinical.recent_vitals", "pii.emergency_contacts"}
    ),
    BreakGlassReasonCode.LIFE_THREATENING_EMERGENCY: frozenset(
        {
            "clinical.allergies",
            "clinical.active_medications",
            "clinical.recent_critical_labs",
            "clinical.blood_group_verified",
            "clinical.recent_vitals",
            "clinical.relevant_diagnoses",
            "pii.emergency_contacts",
        }
    ),
    BreakGlassReasonCode.PATIENT_UNABLE_TO_CONSENT: frozenset(
        {"clinical.allergies", "clinical.active_medications", "clinical.recent_vitals", "pii.emergency_contacts"}
    ),
}

_SAFE_JUSTIFICATION = re.compile(r"^[\w\s.,'()\-/]+$", re.UNICODE)


def validate_justification(value: str) -> str:
    clean = " ".join(value.strip().split())
    if not clean:
        raise ValueError("Clinical justification is required.")
    if len(clean) > MAX_BREAK_GLASS_JUSTIFICATION_LENGTH:
        raise ValueError("Clinical justification is too long.")
    if not _SAFE_JUSTIFICATION.fullmatch(clean):
        raise ValueError("Clinical justification contains unsupported characters.")
    return clean


def approved_break_glass_scope(
    reason_code: BreakGlassReasonCode,
    requested_scope: list[str] | None,
) -> list[str]:
    allowed = BREAK_GLASS_SCOPE_BY_REASON.get(reason_code)
    if not allowed:
        raise ValueError("Break-glass reason is not mapped to an approved scope.")
    if requested_scope is None:
        return sorted(allowed)
    requested = {item.strip() for item in requested_scope if item.strip()}
    if not requested:
        raise ValueError("Requested emergency scope cannot be empty.")
    if not requested.issubset(allowed):
        raise ValueError("Requested emergency scope exceeds the approved policy.")
    return sorted(requested)
