"""Server-owned operation vocabulary for bounded clinical access sessions.

This module is deliberately conservative.  Signed Consent V3 predates the
bounded treatment-session model and its signed context does not contain an
explicit write-operation set.  Therefore current V3 approvals may be mapped
only to the read authority they already represented.  Write operations are
named here for the next protocol step, but MUST NOT be minted from an existing
V3 approval until the patient signs a context that explicitly binds those
operations.
"""

from __future__ import annotations

from enum import Enum
from typing import Final


CLINICAL_ACCESS_POLICY_VERSION: Final[str] = "clinical-access-v1"
SIGNED_CONSENT_V3_PROTOCOL_VERSION: Final[str] = "nexa-consent-v3"
DOCUMENT_PROCESSING_PURPOSE: Final[str] = "document_processing"
DOCUMENT_PROCESSING_SCOPE: Final[str] = "documents"


class ClinicalAccessPolicyError(ValueError):
    """Raised when a consent grant cannot safely map to session operations."""


class ClinicalAccessOperation(str, Enum):
    """Closed server-owned vocabulary for treatment-session authority."""

    READ_CLINICAL_HISTORY = "READ_CLINICAL_HISTORY"
    READ_DOCUMENTS = "READ_DOCUMENTS"
    CREATE_ENCOUNTER = "CREATE_ENCOUNTER"
    WRITE_PRESCRIPTION = "WRITE_PRESCRIPTION"
    WRITE_DIAGNOSIS = "WRITE_DIAGNOSIS"
    WRITE_VITALS = "WRITE_VITALS"
    WRITE_CLINICAL_NOTES = "WRITE_CLINICAL_NOTES"
    ORDER_INVESTIGATION = "ORDER_INVESTIGATION"


_CURRENT_V3_READ_SCOPES: Final[frozenset[str]] = frozenset({"clinical", "full"})
_CURRENT_V3_READ_OPERATIONS: Final[tuple[str, ...]] = (
    ClinicalAccessOperation.READ_CLINICAL_HISTORY.value,
)


def operations_for_signed_v3(*, purpose: str, scope: str) -> tuple[str, ...]:
    """Map an existing Signed Consent V3 approval without widening authority.

    V3 signs purpose/scope, but not an explicit clinical write-operation set.
    Consequently this function intentionally returns read-only authority for
    the two routine V3 scopes and refuses the separate document-processing
    protocol.  Any future write-enabled treatment protocol must have its own
    signed operation binding and must not weaken this mapping in place.
    """

    clean_purpose = purpose.strip()
    clean_scope = scope.strip()
    if not clean_purpose:
        raise ClinicalAccessPolicyError("clinical session purpose is required")
    if clean_purpose == DOCUMENT_PROCESSING_PURPOSE or clean_scope == DOCUMENT_PROCESSING_SCOPE:
        raise ClinicalAccessPolicyError(
            "document-processing authority is not a clinical treatment session"
        )
    if clean_scope not in _CURRENT_V3_READ_SCOPES:
        raise ClinicalAccessPolicyError("unsupported Signed Consent V3 clinical scope")
    return _CURRENT_V3_READ_OPERATIONS


def operation_for_record_category(category: str) -> ClinicalAccessOperation:
    """Return the bounded session operation required by an existing read view."""

    if category in {"clinical_summary", "timeline_view"}:
        return ClinicalAccessOperation.READ_CLINICAL_HISTORY
    raise ClinicalAccessPolicyError("record category is not session-policy mapped")
