"""Value-free, human-disposition lifecycle for exhausted provider retries.

This service deliberately has no provider, document-storage, candidate, routing,
or clinical-record dependency.  An exhausted retry is an operational failure,
not an extraction result.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pipeline import ExtractionFailureQuarantineRecord, ExtractionJob
from app.models.provider_context import ProviderContext
from app.security.audit_context import AuditContext, AuditDomain
from app.security.document_processing_policy import DocumentProcessingOperation
from app.security.erasure_registry import (
    ErasureRegistryUnavailable,
    _PatientErasedSignal,
    check_erasure_registry,
)
from app.services.approved_access_capability import (
    ApprovedAccessStoreUnavailable,
    validate_live_document_processing_request,
)
from app.services.audit_outbox import enqueue_audit_event


RETRY_EXHAUSTED_REASON = "PROVIDER_RETRY_EXHAUSTED"
PENDING = "PENDING"
ESCALATED = "ESCALATED"
DISPOSED = "DISPOSED"
DISPOSITIONS = frozenset(
    {"RETAIN_SOURCE_NO_CLINICAL_COMMIT", "REJECT_PROCESSING_RETAIN_AUDIT"}
)
_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,192}$")


class FailureQuarantineError(RuntimeError):
    """Stable, value-free failure-quarantine error."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _audit_context(tenant_id: uuid.UUID) -> AuditContext:
    return AuditContext.for_tenant(
        tenant_id=str(tenant_id), domain=AuditDomain.PIPELINE
    )


async def create_retry_exhausted_quarantine(
    db: AsyncSession, *, job: ExtractionJob, occurred_at: datetime
) -> ExtractionFailureQuarantineRecord:
    """Create or reconcile the sole operational case for an exhausted job.

    The caller owns the enclosing job-failure transaction; the staged audit event
    therefore succeeds or rolls back with the job and case state.
    """
    if (
        job.tenant_id is None
        or job.status != "quarantined"
        or job.retryable
        or not job.document_id
    ):
        raise FailureQuarantineError("FAILURE_QUARANTINE_JOB_INELIGIBLE")
    existing = (
        await db.execute(
            select(ExtractionFailureQuarantineRecord)
            .where(ExtractionFailureQuarantineRecord.job_id == job.id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if existing is not None:
        if (
            existing.tenant_id != job.tenant_id
            or existing.patient_id != job.patient_id
            or existing.source_document_id != job.document_id
            or existing.reason_code != RETRY_EXHAUSTED_REASON
        ):
            raise FailureQuarantineError("FAILURE_QUARANTINE_BINDING_MISMATCH")
        return existing

    now = occurred_at.astimezone(timezone.utc)
    case = ExtractionFailureQuarantineRecord(
        id=uuid.uuid4(),
        job_id=job.id,
        tenant_id=job.tenant_id,
        patient_id=job.patient_id,
        source_document_id=job.document_id,
        reason_code=RETRY_EXHAUSTED_REASON,
        status=PENDING,
        # Operational review deadline only; it never changes source retention.
        review_deadline=now,
        version=1,
        created_at=now,
        updated_at=now,
    )
    db.add(case)
    await db.flush()
    await enqueue_audit_event(
        db,
        audit_context=_audit_context(case.tenant_id),
        idempotency_key=f"failure-quarantine-created:{case.job_id}",
        actor_id=job.uploader_id or "SYSTEM_PIPELINE",
        event_type="EXTRACTION_FAILURE_QUARANTINED",
        target_id=str(case.id),
        patient_id=str(case.patient_id),
        metadata={"job_id": str(case.job_id), "reason_code": case.reason_code},
    )
    return case


async def escalate_expired_failure_quarantines(
    db: AsyncSession, *, batch_size: int = 25, now: datetime | None = None
) -> int:
    """Claim and escalate due cases using PostgreSQL row locks, never dispose."""
    if not 1 <= batch_size <= 100:
        raise ValueError("batch_size must be between 1 and 100")
    evaluated_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cases = (
        (
            await db.execute(
                select(ExtractionFailureQuarantineRecord)
                .where(
                    ExtractionFailureQuarantineRecord.status == PENDING,
                    ExtractionFailureQuarantineRecord.review_deadline <= evaluated_at,
                )
                .order_by(
                    ExtractionFailureQuarantineRecord.review_deadline,
                    ExtractionFailureQuarantineRecord.id,
                )
                .with_for_update(skip_locked=True)
                .limit(batch_size)
            )
        )
        .scalars()
        .all()
    )
    for case in cases:
        case.status = ESCALATED
        case.escalated_at = evaluated_at
        case.updated_at = evaluated_at
        case.version += 1
        await enqueue_audit_event(
            db,
            audit_context=_audit_context(case.tenant_id),
            idempotency_key=f"failure-quarantine-escalated:{case.id}:{case.version}",
            actor_id="SYSTEM_FAILURE_QUARANTINE",
            event_type="EXTRACTION_FAILURE_QUARANTINE_ESCALATED",
            target_id=str(case.id),
            patient_id=str(case.patient_id),
            metadata={"job_id": str(case.job_id), "version": case.version},
        )
    await db.flush()
    return len(cases)


def _request_hash(
    *, case_id: uuid.UUID, disposition: str, expected_version: int
) -> str:
    payload = json.dumps(
        {
            "case_id": str(case_id),
            "disposition": disposition,
            "expected_version": expected_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


async def _authorize_disposition(
    db: AsyncSession,
    *,
    job: ExtractionJob,
    case: ExtractionFailureQuarantineRecord,
    provider: ProviderContext,
) -> None:
    if "clinical_reviewer" not in set(provider.affiliation.roles or []):
        raise FailureQuarantineError("FAILURE_QUARANTINE_CLINICAL_REVIEWER_REQUIRED")
    if (
        job.tenant_id != provider.hospital.hospital_id
        or job.tenant_id != case.tenant_id
        or job.patient_id != case.patient_id
        or job.document_id != case.source_document_id
        or str(job.authorization_provider_id) != provider.actor_uid
        or not job.consent_request_id
    ):
        raise FailureQuarantineError("FAILURE_QUARANTINE_ACCESS_DENIED")
    try:
        capability = await validate_live_document_processing_request(
            request_id=job.consent_request_id,
            patient_id=str(job.patient_id),
            provider_id=provider.actor_uid,
            hospital_id=str(provider.hospital.hospital_id),
        )
    except ApprovedAccessStoreUnavailable as exc:
        raise FailureQuarantineError("FAILURE_QUARANTINE_CONSENT_UNAVAILABLE") from exc
    if (
        capability is None
        or DocumentProcessingOperation.REVIEW_EXTRACTED_FIELDS.value
        not in capability.allowed_operations
    ):
        raise FailureQuarantineError("FAILURE_QUARANTINE_CONSENT_INACTIVE")
    try:
        await check_erasure_registry(str(job.patient_id), db)
    except _PatientErasedSignal as exc:
        raise FailureQuarantineError(
            "FAILURE_QUARANTINE_ERASURE_ACCESS_BLOCKED"
        ) from exc
    except ErasureRegistryUnavailable as exc:
        raise FailureQuarantineError(
            "FAILURE_QUARANTINE_ERASURE_REGISTRY_UNAVAILABLE"
        ) from exc


async def apply_failure_quarantine_disposition(
    db: AsyncSession,
    *,
    case_id: uuid.UUID,
    provider: ProviderContext,
    disposition: str,
    expected_version: int,
    idempotency_key: str,
) -> ExtractionFailureQuarantineRecord:
    """Apply exactly one terminal, authorized, non-clinical disposition."""
    if disposition not in DISPOSITIONS:
        raise FailureQuarantineError("FAILURE_QUARANTINE_DISPOSITION_INVALID")
    if expected_version < 1:
        raise FailureQuarantineError("FAILURE_QUARANTINE_VERSION_CONFLICT")
    if _IDEMPOTENCY_RE.fullmatch(idempotency_key) is None:
        raise FailureQuarantineError("FAILURE_QUARANTINE_IDEMPOTENCY_KEY_INVALID")
    case = (
        await db.execute(
            select(ExtractionFailureQuarantineRecord)
            .where(ExtractionFailureQuarantineRecord.id == case_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if case is None:
        raise FailureQuarantineError("FAILURE_QUARANTINE_NOT_FOUND")
    request_hash = _request_hash(
        case_id=case_id, disposition=disposition, expected_version=expected_version
    )
    if case.disposition_idempotency_key == idempotency_key:
        if case.disposition_request_hash == request_hash:
            return case
        raise FailureQuarantineError("FAILURE_QUARANTINE_IDEMPOTENCY_KEY_REUSED")
    if case.status == DISPOSED:
        raise FailureQuarantineError("FAILURE_QUARANTINE_ALREADY_DISPOSED")
    if case.status != ESCALATED:
        raise FailureQuarantineError("FAILURE_QUARANTINE_NOT_ESCALATED")
    if case.version != expected_version:
        raise FailureQuarantineError("FAILURE_QUARANTINE_VERSION_CONFLICT")
    job = (
        await db.execute(select(ExtractionJob).where(ExtractionJob.id == case.job_id))
    ).scalar_one_or_none()
    if job is None:
        raise FailureQuarantineError("FAILURE_QUARANTINE_BINDING_MISMATCH")
    await _authorize_disposition(db, job=job, case=case, provider=provider)

    now = datetime.now(timezone.utc)
    case.status = DISPOSED
    case.disposition = disposition
    case.disposed_at = now
    case.disposed_by_provider_id = provider.provider.provider_id
    case.disposition_idempotency_key = idempotency_key
    case.disposition_request_hash = request_hash
    case.updated_at = now
    case.version += 1
    await enqueue_audit_event(
        db,
        audit_context=_audit_context(case.tenant_id),
        idempotency_key=f"failure-quarantine-disposed:{case.id}:{case.version}",
        actor_id=provider.actor_uid,
        event_type="EXTRACTION_FAILURE_QUARANTINE_DISPOSITION_APPLIED",
        target_id=str(case.id),
        patient_id=str(case.patient_id),
        metadata={
            "job_id": str(case.job_id),
            "disposition": disposition,
            "version": case.version,
        },
    )
    await db.flush()
    return case
