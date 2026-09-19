"""Pure current-state policy for prescribing-specific professional authority."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from app.models.provider import (
    PrescribingEligibilityDecision,
    PrescribingEligibilitySourceType,
    PrescribingEligibilityStatus,
    PrescribingPractitionerClass,
    ProfessionalVerification,
    ProfessionalVerificationStatus,
)

PRESCRIBER_ELIGIBILITY_POLICY_VERSION = "prescriber-eligibility/v1"

_ALLOWED_POSITIVE_SOURCES = frozenset(
    {
        PrescribingEligibilitySourceType.NMR,
        PrescribingEligibilitySourceType.SMR,
        PrescribingEligibilitySourceType.COMPETENT_MEDICAL_COUNCIL,
    }
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone-aware datetime required")
    return value.astimezone(timezone.utc)


def current_prescribing_eligibility_denial(
    professional: ProfessionalVerification | None,
    decision: PrescribingEligibilityDecision | None,
    *,
    now: datetime,
) -> str | None:
    """Return a stable denial code, or None when prescribing is current."""

    moment = _aware_utc(now)
    if professional is None:
        return "PRESCRIBING_ELIGIBILITY_REQUIRED"
    try:
        professional_status = ProfessionalVerificationStatus(professional.status)
    except (TypeError, ValueError):
        return "PRESCRIBING_ELIGIBILITY_INTEGRITY_FAILURE"

    if professional_status is not ProfessionalVerificationStatus.VERIFIED:
        return "PRESCRIBING_ELIGIBILITY_NOT_CURRENT"
    if professional.verified_at is None:
        return "PRESCRIBING_ELIGIBILITY_INTEGRITY_FAILURE"
    try:
        verified_at = _aware_utc(professional.verified_at)
    except ValueError:
        return "PRESCRIBING_ELIGIBILITY_INTEGRITY_FAILURE"
    if verified_at > moment:
        return "PRESCRIBING_ELIGIBILITY_INTEGRITY_FAILURE"
    if professional.registration_valid_from is not None:
        try:
            if _aware_utc(professional.registration_valid_from) > moment:
                return "PRESCRIBING_ELIGIBILITY_NOT_CURRENT"
        except ValueError:
            return "PRESCRIBING_ELIGIBILITY_INTEGRITY_FAILURE"
    if professional.registration_valid_until is not None:
        try:
            if _aware_utc(professional.registration_valid_until) <= moment:
                return "PRESCRIBING_ELIGIBILITY_NOT_CURRENT"
        except ValueError:
            return "PRESCRIBING_ELIGIBILITY_INTEGRITY_FAILURE"
    if professional.next_review_at is not None:
        try:
            if _aware_utc(professional.next_review_at) <= moment:
                return "PRESCRIBING_ELIGIBILITY_NOT_CURRENT"
        except ValueError:
            return "PRESCRIBING_ELIGIBILITY_INTEGRITY_FAILURE"
    if professional.authoritative_adverse_signal_at is not None:
        return "PRESCRIBING_ELIGIBILITY_NOT_CURRENT"
    if professional.identity_binding_status != "MATCHED":
        return "PRESCRIBING_ELIGIBILITY_INTEGRITY_FAILURE"
    if not professional.registration_authority_code or not professional.registration_number_normalized:
        return "PRESCRIBING_ELIGIBILITY_INTEGRITY_FAILURE"

    if decision is None:
        return "PRESCRIBING_ELIGIBILITY_REQUIRED"
    if decision.provider_id != professional.provider_id:
        return "PRESCRIBING_ELIGIBILITY_INTEGRITY_FAILURE"
    if decision.professional_verification_id != professional.id:
        return "PRESCRIBING_ELIGIBILITY_NOT_CURRENT"
    if decision.professional_verification_version != professional.version:
        return "PRESCRIBING_ELIGIBILITY_NOT_CURRENT"
    if decision.registration_authority_code != professional.registration_authority_code:
        return "PRESCRIBING_ELIGIBILITY_NOT_CURRENT"
    if decision.registration_number_normalized != professional.registration_number_normalized:
        return "PRESCRIBING_ELIGIBILITY_NOT_CURRENT"
    if decision.reviewer_provider_id == decision.provider_id:
        return "PRESCRIBING_ELIGIBILITY_INTEGRITY_FAILURE"
    if decision.policy_version != PRESCRIBER_ELIGIBILITY_POLICY_VERSION:
        return "PRESCRIBING_ELIGIBILITY_NOT_CURRENT"
    if not _SHA256_RE.fullmatch(decision.evidence_sha256 or ""):
        return "PRESCRIBING_ELIGIBILITY_INTEGRITY_FAILURE"
    if not isinstance(decision.source_reference, str) or not decision.source_reference.strip():
        return "PRESCRIBING_ELIGIBILITY_INTEGRITY_FAILURE"

    try:
        state = PrescribingEligibilityStatus(decision.status)
        practitioner_class = PrescribingPractitionerClass(decision.practitioner_class)
        source_type = PrescribingEligibilitySourceType(decision.source_type)
        checked_at = _aware_utc(decision.checked_at)
        valid_until = _aware_utc(decision.valid_until)
    except (TypeError, ValueError):
        return "PRESCRIBING_ELIGIBILITY_INTEGRITY_FAILURE"

    if checked_at > moment or valid_until <= checked_at:
        return "PRESCRIBING_ELIGIBILITY_INTEGRITY_FAILURE"
    if valid_until <= moment:
        return "PRESCRIBING_ELIGIBILITY_NOT_CURRENT"
    if state is not PrescribingEligibilityStatus.ELIGIBLE:
        return (
            "PRESCRIBING_ELIGIBILITY_RESTRICTED"
            if state is PrescribingEligibilityStatus.RESTRICTED
            else "PRESCRIBING_ELIGIBILITY_NOT_CURRENT"
        )
    if practitioner_class is not PrescribingPractitionerClass.FULL_RMP_MODERN_MEDICINE:
        return "PRESCRIBING_ELIGIBILITY_RESTRICTED"
    if source_type not in _ALLOWED_POSITIVE_SOURCES:
        return "PRESCRIBING_ELIGIBILITY_RESTRICTED"
    if decision.restriction_code is not None:
        return "PRESCRIBING_ELIGIBILITY_RESTRICTED"

    return None
