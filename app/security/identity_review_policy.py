"""Closed server policy for metadata-only identity quarantine review."""

from __future__ import annotations

from enum import StrEnum

IDENTITY_REVIEW_POLICY_VERSION = "identity-review/1.0"
IDENTITY_REVIEW_ROLE = "identity_reviewer"


class IdentityReviewOperation(StrEnum):
    """Authority that is intentionally separate from document grants."""

    CREATE_CASE = "CREATE_CASE"
    LIST_CASES = "LIST_CASES"
    READ_CASE = "READ_CASE"
    CLAIM_CASE = "CLAIM_CASE"
    RECOVER_SESSION = "RECOVER_SESSION"
    SUBMIT_DISPOSITION = "SUBMIT_DISPOSITION"


IDENTITY_REVIEW_OPERATIONS = frozenset(IdentityReviewOperation)
