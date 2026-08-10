"""Fail-closed authorization for the independent identity-review boundary."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.provider_context import ProviderContext
from app.security.document_processing_policy import DocumentProcessingOperation
from app.security.erasure_registry import (
    ErasureRegistryUnavailable,
    _PatientErasedSignal,
    check_erasure_registry,
)
from app.security.identity_review_policy import (
    IDENTITY_REVIEW_ROLE,
    IdentityReviewOperation,
)
from app.services.approved_access_capability import (
    ApprovedAccessCapability,
    ApprovedAccessStoreUnavailable,
    validate_document_processing_access,
)


class IdentityReviewGateError(RuntimeError):
    """Stable, value-free authorization failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _assert_current_identity_reviewer(provider: ProviderContext) -> None:
    if IDENTITY_REVIEW_ROLE not in provider.affiliation.roles:
        raise IdentityReviewGateError("IDENTITY_REVIEW_ROLE_REQUIRED")
    now = datetime.now(timezone.utc)
    valid_from = provider.affiliation.valid_from
    valid_until = provider.affiliation.valid_until
    if (
        (valid_from is not None and valid_from.tzinfo is None)
        or (valid_until is not None and valid_until.tzinfo is None)
        or (valid_from is not None and now < valid_from)
        or (valid_until is not None and now >= valid_until)
    ):
        raise IdentityReviewGateError("IDENTITY_REVIEW_ACCESS_DENIED")


def assert_identity_review_separation(
    *,
    provider: ProviderContext,
    original_uploader_id: str | None,
    original_authorization_provider_id: str | None,
) -> None:
    """An uploader/original workflow provider can never review their own job."""
    if provider.actor_uid in {
        str(value)
        for value in (original_uploader_id, original_authorization_provider_id)
        if value is not None
    }:
        raise IdentityReviewGateError("IDENTITY_REVIEW_SELF_REVIEW_FORBIDDEN")


async def authorize_identity_review(
    db: AsyncSession,
    *,
    token: str | None,
    patient_id: str,
    tenant_id: str,
    provider: ProviderContext,
    operation: IdentityReviewOperation,
) -> ApprovedAccessCapability:
    """Authorize dedicated review using the reviewer's own live capability.

    ``READ_JOB_STATUS`` proves the underlying patient/hospital consent-bound
    document access. The dedicated enum controls the additional review action;
    it is deliberately not added to the generic document grant.
    """
    del operation  # Closed policy selection is enforced by the caller's API/service.
    _assert_current_identity_reviewer(provider)
    if str(provider.hospital.hospital_id) != str(tenant_id):
        raise IdentityReviewGateError("IDENTITY_REVIEW_ACCESS_DENIED")
    if not token:
        raise IdentityReviewGateError("IDENTITY_REVIEW_CONSENT_INACTIVE")
    try:
        capability = await validate_document_processing_access(
            token=token,
            patient_id=str(patient_id),
            provider_id=provider.actor_uid,
            hospital_id=str(provider.hospital.hospital_id),
            required_operation=DocumentProcessingOperation.READ_JOB_STATUS,
        )
    except ApprovedAccessStoreUnavailable as exc:
        raise IdentityReviewGateError("IDENTITY_REVIEW_ACCESS_DENIED") from exc
    if capability is None:
        raise IdentityReviewGateError("IDENTITY_REVIEW_CONSENT_INACTIVE")
    if (
        capability.patient_id != str(patient_id)
        or capability.clinician_id != provider.actor_uid
        or capability.hospital_id != str(tenant_id)
    ):
        raise IdentityReviewGateError("IDENTITY_REVIEW_ACCESS_DENIED")
    try:
        await check_erasure_registry(str(patient_id), db)
    except _PatientErasedSignal as exc:
        raise IdentityReviewGateError("IDENTITY_REVIEW_ERASURE_ACCESS_BLOCKED") from exc
    except ErasureRegistryUnavailable as exc:
        raise IdentityReviewGateError(
            "IDENTITY_REVIEW_ERASURE_REGISTRY_UNAVAILABLE"
        ) from exc
    return capability


__all__ = [
    "IdentityReviewGateError",
    "assert_identity_review_separation",
    "authorize_identity_review",
]
