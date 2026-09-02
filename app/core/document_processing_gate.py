"""Shared fail-closed authorization for document-processing operations."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pipeline import ExtractionJob
from app.models.provider_context import ProviderContext
from app.observability.audit_ledger import append_audit_log_or_503
from app.security.audit_context import AuditContext, AuditDomain, current_audit_context
from app.security.document_processing_policy import DocumentProcessingOperation
from app.security.clinical_policy import CLINICAL_CONTACT_ASSURANCE_POLICY
from app.security.provider_capabilities import ClinicalCapability
from app.services.approved_access_capability import (
    ApprovedAccessCapability,
    ApprovedAccessStoreUnavailable,
    validate_document_processing_access,
    validate_live_document_processing_request,
)
from app.services.clinical_eligibility import (
    ClinicalAuthenticationMethod,
    ClinicalEligibilityService,
    ClinicalEligibilityUnavailable,
    DelegatedInitiationAssurance,
)
from app.services.audit_outbox import enqueue_audit_event


# These are the only parent workflow states in which delegated extraction can
# read a source, contact an external provider, or consume a provider result.
# ``queued`` remains for durable pre-Slice-2 workflows and retryable jobs;
# newly-created jobs use ``extraction_pending`` before the worker marks them
# ``extracting``.
DELEGATED_PROCESSING_JOB_STATES = frozenset(
    {"queued", "extraction_pending", "extracting", "extraction_failed_retryable"}
)


class DelegatedClinicalTrustError(RuntimeError):
    """Safe worker-facing denial for delegated clinical work."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class DelegatedClinicalTrustQuarantineUnavailable(RuntimeError):
    """The required value-free denial record could not be made durable."""


async def quarantine_delegated_clinical_trust_denial(
    *, db: AsyncSession, job_id: UUID, error_code: str
) -> ExtractionJob:
    """Atomically quarantine a delegated workflow and its denial outbox event.

    Protected continuation is forbidden if this transaction cannot commit.  The
    event deliberately carries only workflow/lifecycle facts, never source
    content, a session token, or patient identity in its metadata.
    """

    await db.rollback()
    job = (
        await db.execute(
            select(ExtractionJob).where(ExtractionJob.id == job_id).with_for_update()
        )
    ).scalar_one_or_none()
    if job is None:
        raise DelegatedClinicalTrustQuarantineUnavailable(
            "DELEGATED_CLINICAL_TRUST_JOB_UNAVAILABLE"
        )
    if job.status == "quarantined":
        # Quarantine is a first-cause terminal disposition.  Because the row
        # and its required outbox event are committed in one transaction, a
        # durable terminal row proves that the original transaction completed;
        # a failed transaction cannot leave this state behind.  Preserve the
        # original cause and timestamp instead of deriving a later lifecycle
        # error and emitting a replacement event.
        if job.retryable or job.error_code is None or job.completed_at is None:
            raise DelegatedClinicalTrustQuarantineUnavailable(
                "DELEGATED_CLINICAL_TRUST_QUARANTINE_INTEGRITY_FAILURE"
            )
        return job
    job.status = "quarantined"
    job.error_code = error_code
    job.retryable = False
    job.completed_at = datetime.now(timezone.utc)
    try:
        await enqueue_audit_event(
            db,
            audit_context=AuditContext.for_tenant(
                tenant_id=str(job.tenant_id), domain=AuditDomain.PIPELINE
            ),
            idempotency_key=(
                f"delegated-clinical-trust:{job.id}:{job.attempt_count}:{error_code}"
            ),
            actor_id=job.uploader_id or "SYSTEM_PIPELINE",
            event_type="DELEGATED_CLINICAL_TRUST_DENIED",
            target_id=str(job.id),
            patient_id=str(job.patient_id),
            status="DENIED",
            metadata={
                "error_code": error_code,
                "attempt_count": job.attempt_count,
                "capability": ClinicalCapability.DOCUMENTS_PROCESS.value,
                "policy_version": str(
                    job.authorization_assurance_policy_version or "unavailable"
                ),
            },
        )
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise DelegatedClinicalTrustQuarantineUnavailable(
            "DELEGATED_CLINICAL_TRUST_AUDIT_UNAVAILABLE"
        ) from exc
    return job


async def recheck_delegated_document_processing_trust(*, job, db: AsyncSession) -> None:
    """Fail closed before each protected asynchronous extraction step.

    A stored initiation assurance is necessary but never sufficient: this
    function reloads provider/facility/affiliation trust and validates the
    currently live document-processing grant on every invocation.
    """

    if getattr(job, "status", None) not in DELEGATED_PROCESSING_JOB_STATES:
        raise DelegatedClinicalTrustError("DELEGATED_WORKFLOW_STATE_INVALID")

    try:
        provider_id = UUID(str(job.authorization_provider_id))
        hospital_id = UUID(str(job.tenant_id))
        consent_request_id = UUID(str(job.consent_request_id))
        initiated_at = job.authorization_initiated_at
        mfa_verified_at = job.authorization_mfa_verified_at
        method = ClinicalAuthenticationMethod(job.authorization_authentication_method)
    except (TypeError, ValueError, AttributeError) as exc:
        raise DelegatedClinicalTrustError(
            "DELEGATED_INITIATION_ASSURANCE_INVALID"
        ) from exc
    if initiated_at is None or mfa_verified_at is None:
        raise DelegatedClinicalTrustError("DELEGATED_INITIATION_ASSURANCE_REQUIRED")

    try:
        live_capability = await validate_live_document_processing_request(
            request_id=str(job.consent_request_id),
            patient_id=str(job.patient_id),
            provider_id=str(job.authorization_provider_id),
            hospital_id=str(job.tenant_id),
        )
    except ApprovedAccessStoreUnavailable as exc:
        raise DelegatedClinicalTrustError(
            "DELEGATED_CLINICAL_TRUST_UNAVAILABLE"
        ) from exc

    assurance = DelegatedInitiationAssurance(
        initiated_by_provider_id=provider_id,
        initiated_hospital_id=hospital_id,
        initiated_at=initiated_at,
        authentication_method=method,
        mfa_verified_at=mfa_verified_at,
        assurance_policy_version=str(job.authorization_assurance_policy_version or ""),
        workflow_id=job.id,
        consent_request_id=consent_request_id,
        required_capability=ClinicalCapability.DOCUMENTS_PROCESS,
        workflow_authorization_current=live_capability is not None,
    )
    try:
        result = await ClinicalEligibilityService(
            contact_assurance_policy=CLINICAL_CONTACT_ASSURANCE_POLICY
        ).evaluate_delegated(
            db,
            provider_id,
            hospital_id,
            assurance,
            ClinicalCapability.DOCUMENTS_PROCESS,
        )
    except ClinicalEligibilityUnavailable as exc:
        raise DelegatedClinicalTrustError(
            "DELEGATED_CLINICAL_TRUST_UNAVAILABLE"
        ) from exc
    if not result.allowed:
        raise DelegatedClinicalTrustError(
            result.denial_code.value
            if result.denial_code is not None
            else "DELEGATED_CLINICAL_TRUST_DENIED"
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
