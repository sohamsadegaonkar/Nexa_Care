"""Server-owned clinical capability vocabulary and legacy role mapping."""

from __future__ import annotations

from enum import Enum
from typing import Final


class ClinicalCapability(str, Enum):
    PATIENT_DISCOVER = "patient.discover"
    CONSENT_REQUEST = "consent.request"
    RECORD_READ = "record.read"
    DOCUMENTS_UPLOAD = "documents.upload"
    DOCUMENTS_PROCESS = "documents.process"
    DOCUMENTS_REVIEW = "documents.review"
    DOCUMENTS_COMMIT = "documents.commit"
    EMERGENCY_ATTEMPT = "emergency.attempt"
    PRESCRIBE_MEDICATION = "prescribe.medication"


ALL_CLINICAL_CAPABILITIES: Final[frozenset[ClinicalCapability]] = frozenset(
    ClinicalCapability
)

# Prescribing is never role-derived.  This set contains only capabilities that
# legacy affiliation roles may contribute after the independent trust checks.
ROLE_DERIVED_CLINICAL_CAPABILITIES: Final[frozenset[ClinicalCapability]] = frozenset(
    capability
    for capability in ClinicalCapability
    if capability is not ClinicalCapability.PRESCRIBE_MEDICATION
)

# This compatibility mapping is intentionally narrow and server owned.  A role
# can contribute capabilities only after every independent trust check passes.
LEGACY_ROLE_CAPABILITIES: Final[dict[str, frozenset[ClinicalCapability]]] = {
    "clinician": ROLE_DERIVED_CLINICAL_CAPABILITIES,
    "clinical_reviewer": frozenset(
        {
            ClinicalCapability.DOCUMENTS_REVIEW,
            ClinicalCapability.DOCUMENTS_COMMIT,
        }
    ),
}


def capabilities_for_affiliation_roles(roles: object) -> frozenset[ClinicalCapability]:
    """Map only known server-side legacy roles to fixed capabilities.

    Non-list values and client-supplied capability-like strings are ignored.
    """

    if not isinstance(roles, list):
        return frozenset()
    capabilities: set[ClinicalCapability] = set()
    for role in roles:
        if isinstance(role, str):
            capabilities.update(LEGACY_ROLE_CAPABILITIES.get(role.strip().lower(), ()))
    return frozenset(capabilities)


def capability_is_granted(roles: object, capability: ClinicalCapability) -> bool:
    """Return whether a typed, server-owned capability is granted."""

    if not isinstance(capability, ClinicalCapability):
        return False
    return capability in capabilities_for_affiliation_roles(roles)
