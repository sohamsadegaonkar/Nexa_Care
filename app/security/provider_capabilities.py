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


ALL_CLINICAL_CAPABILITIES: Final[frozenset[ClinicalCapability]] = frozenset(
    ClinicalCapability
)

# This compatibility mapping is intentionally narrow and server owned.  A role
# can contribute capabilities only after every independent trust check passes.
LEGACY_ROLE_CAPABILITIES: Final[dict[str, frozenset[ClinicalCapability]]] = {
    "clinician": ALL_CLINICAL_CAPABILITIES,
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
