"""Shared fail-closed authorization for document-processing operations."""

from __future__ import annotations

from fastapi import HTTPException, status

from app.models.provider_context import ProviderContext
from app.observability.audit_ledger import append_audit_log_or_503
from app.security.audit_context import AuditDomain, current_audit_context
from app.security.document_processing_policy import DocumentProcessingOperation
from app.services.approved_access_capability import (
    ApprovedAccessCapability,
    ApprovedAccessStoreUnavailable,
    validate_document_processing_access,
)


async def authorize_document_processing(
    *,
    token: str | None,
    patient_id: str,
    provider: ProviderContext,
    operation: DocumentProcessingOperation,
    consent_request_id: str | None = None,
) -> ApprovedAccessCapability:
    """Authorize a trusted operation against server-derived bindings."""
    safe_metadata = {
        "patient_id": str(patient_id),
        "provider_id": provider.actor_uid,
        "hospital_id": str(provider.hospital_id),
        "consent_request_id": consent_request_id,
        "operation": operation.value,
    }
    try:
        capability = (
            await validate_document_processing_access(
                token=token or "",
                patient_id=str(patient_id),
                provider_id=provider.actor_uid,
                hospital_id=str(provider.hospital_id),
                required_operation=operation,
                expected_request_id=consent_request_id,
            )
            if token
            else None
        )
    except ApprovedAccessStoreUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "DOCUMENT_PROCESSING_SERVICE_UNAVAILABLE"},
        ) from exc

    if capability is None:
        await append_audit_log_or_503(
            audit_context=current_audit_context(AuditDomain.PIPELINE),
            actor_uid=provider.actor_uid,
            event_type="DOCUMENT_PROCESSING_AUTHORIZATION_DENIED",
            target_id=str(patient_id),
            status="REJECTED",
            metadata=safe_metadata,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": "DOCUMENT_PROCESSING_ACCESS_REQUIRED"},
        )

    await append_audit_log_or_503(
        audit_context=current_audit_context(AuditDomain.PIPELINE),
        actor_uid=provider.actor_uid,
        event_type="DOCUMENT_PROCESSING_AUTHORIZATION_ALLOWED",
        target_id=str(patient_id),
        status="SUCCESS",
        metadata=safe_metadata,
    )
    return capability


def assert_job_authorization_binding(
    *,
    job,
    capability: ApprovedAccessCapability,
    provider: ProviderContext,
) -> None:
    """Reject legacy or cross-bound jobs after loading the entity server-side."""
    if not all(
        (
            job.tenant_id,
            job.authorization_provider_id,
            job.consent_request_id,
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": "JOB_AUTHORIZATION_MISMATCH"},
        )
    if str(job.patient_id) != capability.patient_id:
        code = "CROSS_PATIENT_JOB_ACCESS"
    elif str(job.tenant_id) != str(provider.hospital_id):
        code = "CROSS_TENANT_JOB_ACCESS"
    elif str(job.authorization_provider_id) != provider.actor_uid:
        code = "CROSS_PROVIDER_JOB_ACCESS"
    elif str(job.consent_request_id) != capability.request_id:
        code = "JOB_AUTHORIZATION_MISMATCH"
    else:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"error_code": code},
    )
