"""Versioned clinical-governance policy for emergency access.

Break-glass grants are scoped to *clinical categories* (see
``app.security.clinical_categories``), never to arbitrary field paths and
never to a "purpose". Purpose (``EMERGENCY``) and category scope are
independent claims on the capability -- see ``consent_engine._matches``.
"""

from __future__ import annotations

import re
from enum import Enum

from app.security.clinical_categories import (
    CLINICAL_CATEGORY_PROTOCOL_VERSION,
    ClinicalCategory,
    UnsupportedClinicalCategoryError,
    narrow_categories,
)

BREAK_GLASS_POLICY_VERSION = "2026-07-v2"
BREAK_GLASS_REASON_CODE_VERSION = "v1"
MAX_BREAK_GLASS_JUSTIFICATION_LENGTH = 500


class BreakGlassReasonCode(str, Enum):
    UNCONSCIOUS_PATIENT = "UNCONSCIOUS_PATIENT"
    LIFE_THREATENING_EMERGENCY = "LIFE_THREATENING_EMERGENCY"
    PATIENT_UNABLE_TO_CONSENT = "PATIENT_UNABLE_TO_CONSENT"
    CARDIAC_ARREST = "CARDIAC_ARREST"
    ANAPHYLAXIS = "ANAPHYLAXIS"
    SURGICAL_EMERGENCY = "SURGICAL_EMERGENCY"
    PATIENT_INCAPACITATED = "PATIENT_INCAPACITATED"
    SYSTEM_OR_CONSENT_SERVICE_UNAVAILABLE = "SYSTEM_OR_CONSENT_SERVICE_UNAVAILABLE"
    OTHER_CLINICALLY_JUSTIFIED_EMERGENCY = "OTHER_CLINICALLY_JUSTIFIED_EMERGENCY"


# These categories are an upper bound: a client may request fewer but never
# more. Every member must be a ``ClinicalCategory`` backed by a real,
# authoritative data source. Emergency contacts are intentionally absent --
# there is no encrypted, verified emergency-contact source in this
# repository yet. Add it here only once one exists.
BREAK_GLASS_CATEGORIES_BY_REASON: dict[BreakGlassReasonCode, frozenset[ClinicalCategory]] = {
    BreakGlassReasonCode.UNCONSCIOUS_PATIENT: frozenset(
        {ClinicalCategory.ALLERGIES, ClinicalCategory.ACTIVE_MEDICATIONS, ClinicalCategory.VITALS}
    ),
    BreakGlassReasonCode.LIFE_THREATENING_EMERGENCY: frozenset(
        {
            ClinicalCategory.ALLERGIES,
            ClinicalCategory.ACTIVE_MEDICATIONS,
            ClinicalCategory.LAB_RESULTS,
            ClinicalCategory.BLOOD_GROUP,
            ClinicalCategory.VITALS,
            ClinicalCategory.DIAGNOSES,
        }
    ),
    BreakGlassReasonCode.PATIENT_UNABLE_TO_CONSENT: frozenset(
        {ClinicalCategory.ALLERGIES, ClinicalCategory.ACTIVE_MEDICATIONS, ClinicalCategory.VITALS}
    ),
    BreakGlassReasonCode.CARDIAC_ARREST: frozenset(
        {
            ClinicalCategory.ALLERGIES,
            ClinicalCategory.ACTIVE_MEDICATIONS,
            ClinicalCategory.LAB_RESULTS,
            ClinicalCategory.BLOOD_GROUP,
            ClinicalCategory.VITALS,
            ClinicalCategory.DIAGNOSES,
        }
    ),
    BreakGlassReasonCode.ANAPHYLAXIS: frozenset(
        {ClinicalCategory.ALLERGIES, ClinicalCategory.ACTIVE_MEDICATIONS, ClinicalCategory.VITALS}
    ),
    BreakGlassReasonCode.SURGICAL_EMERGENCY: frozenset(
        {
            ClinicalCategory.ALLERGIES,
            ClinicalCategory.ACTIVE_MEDICATIONS,
            ClinicalCategory.LAB_RESULTS,
            ClinicalCategory.BLOOD_GROUP,
            ClinicalCategory.VITALS,
            ClinicalCategory.DIAGNOSES,
        }
    ),
    BreakGlassReasonCode.PATIENT_INCAPACITATED: frozenset(
        {ClinicalCategory.ALLERGIES, ClinicalCategory.ACTIVE_MEDICATIONS, ClinicalCategory.VITALS}
    ),
    BreakGlassReasonCode.SYSTEM_OR_CONSENT_SERVICE_UNAVAILABLE: frozenset(
        {ClinicalCategory.ALLERGIES, ClinicalCategory.ACTIVE_MEDICATIONS, ClinicalCategory.VITALS}
    ),
    BreakGlassReasonCode.OTHER_CLINICALLY_JUSTIFIED_EMERGENCY: frozenset(
        {ClinicalCategory.ALLERGIES, ClinicalCategory.ACTIVE_MEDICATIONS, ClinicalCategory.VITALS}
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
    """Return the approved, canonical clinical categories for this grant.

    ``requested_scope`` may only narrow the reason code's approved category
    set -- it can never expand it. Any unknown category value fails the
    whole request closed with ``UnsupportedClinicalCategoryError``.
    """

    approved = BREAK_GLASS_CATEGORIES_BY_REASON.get(reason_code)
    if not approved:
        raise ValueError("Break-glass reason is not mapped to an approved category set.")

    categories = narrow_categories(approved, requested_scope)
    return [category.value for category in categories]


__all__ = [
    "BREAK_GLASS_POLICY_VERSION",
    "BREAK_GLASS_REASON_CODE_VERSION",
    "MAX_BREAK_GLASS_JUSTIFICATION_LENGTH",
    "BreakGlassReasonCode",
    "BREAK_GLASS_CATEGORIES_BY_REASON",
    "CLINICAL_CATEGORY_PROTOCOL_VERSION",
    "UnsupportedClinicalCategoryError",
    "validate_justification",
    "approved_break_glass_scope",
]