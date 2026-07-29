"""Consent-bound human adjudication and clinical commit boundary."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.adjudication import (
    ADJUDICATION_CONTRACT_VERSION,
    ADJUDICATION_POLICY_VERSION,
    IDEMPOTENCY_KEY_PATTERN,
    REVIEW_SESSION_PATTERN,
    AdjudicatedClinicalField,
    AdjudicationOutcome,
    AdjudicationReasonCode,
    AdjudicationSubmission,
    LabClinicalField,
    VitalClinicalField,
)
from app.models.patient_records import LabResult, TimelineEvent, Vitals
from app.models.pipeline import (
    AdjudicationCaseRecord,
    AdjudicationSubmissionRecord,
    DocumentStorage,
    ExtractionDecisionRecord,
    ExtractionJob,
    ExtractionRoutingRecord,
)
from app.models.provider_context import ProviderContext
from app.security.audit_context import AuditContext, AuditDomain
from app.security.document_processing_policy import DocumentProcessingOperation
from app.security.erasure_registry import (
    ErasureRegistryUnavailable,
    _PatientErasedSignal,
    check_erasure_registry,
)
from app.services.approved_access_capability import (
    validate_live_document_processing_request,
)
from app.services.audit_outbox import enqueue_audit_event
from app.services.document_storage import get_document_storage

REVIEWER_ROLES = frozenset({"clinician", "clinical_reviewer"})
_FIELD_ADAPTER = TypeAdapter(list[AdjudicatedClinicalField])
_REASON_ADAPTER = TypeAdapter(list[AdjudicationReasonCode])


class AdjudicationError(RuntimeError):
    """Stable, value-free adjudication failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _reviewer_role(provider: ProviderContext) -> str:
    roles = REVIEWER_ROLES.intersection(provider.affiliation.roles)
    if not roles:
        raise AdjudicationError("ADJUDICATION_ROLE_REQUIRED")
    return sorted(roles)[0]


async def _live_access(
    db: AsyncSession,
    *,
    job: ExtractionJob,
    provider: ProviderContext,
    operation: DocumentProcessingOperation,
) -> None:
    if (
        job.tenant_id != provider.hospital.hospital_id
        or str(job.authorization_provider_id) != provider.actor_uid
        or not job.consent_request_id
    ):
        raise AdjudicationError("ADJUDICATION_ACCESS_DENIED")
    capability = await validate_live_document_processing_request(
        request_id=job.consent_request_id,
        patient_id=str(job.patient_id),
        provider_id=provider.actor_uid,
        hospital_id=str(provider.hospital.hospital_id),
    )
    if capability is None or operation.value not in capability.allowed_operations:
        raise AdjudicationError("ADJUDICATION_CONSENT_INACTIVE")
    try:
        await check_erasure_registry(str(job.patient_id), db)
    except _PatientErasedSignal as exc:
        await enqueue_audit_event(
            db,
            audit_context=_audit_context(job.tenant_id),
            idempotency_key=f"adjudication-access-blocked:{operation.value}:{uuid.uuid4()}",
            actor_id=provider.actor_uid,
            event_type="ADJUDICATION_ACCESS_REJECTED",
            target_id=str(job.id),
            patient_id=str(job.patient_id),
            metadata={"operation": operation.value, "reason": "ERASURE_ACCESS_BLOCKED"},
        )
        raise AdjudicationError("ADJUDICATION_ERASURE_ACCESS_BLOCKED") from exc
    except ErasureRegistryUnavailable as exc:
        await enqueue_audit_event(
            db,
            audit_context=_audit_context(job.tenant_id),
            idempotency_key=f"adjudication-registry-unavailable:{operation.value}:{uuid.uuid4()}",
            actor_id=provider.actor_uid,
            event_type="ADJUDICATION_ACCESS_REJECTED",
            target_id=str(job.id),
            patient_id=str(job.patient_id),
            metadata={
                "operation": operation.value,
                "reason": "ERASURE_REGISTRY_UNAVAILABLE",
            },
        )
        raise AdjudicationError("ADJUDICATION_ERASURE_REGISTRY_UNAVAILABLE") from exc


def _audit_context(tenant_id: uuid.UUID) -> AuditContext:
    return AuditContext.for_tenant(
        tenant_id=str(tenant_id), domain=AuditDomain.PIPELINE
    )


def _operation_hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _validate_identifier(
    value: str, *, pattern: str, minimum: int, maximum: int, error_code: str
) -> str:
    if not minimum <= len(value) <= maximum or re.fullmatch(pattern, value) is None:
        raise AdjudicationError(error_code)
    return value


def _validate_session(value: str) -> str:
    return _validate_identifier(
        value,
        pattern=REVIEW_SESSION_PATTERN,
        minimum=8,
        maximum=96,
        error_code="ADJUDICATION_SESSION_INVALID",
    )


def _validate_idempotency_key(value: str) -> str:
    return _validate_identifier(
        value,
        pattern=IDEMPOTENCY_KEY_PATTERN,
        minimum=8,
        maximum=192,
        error_code="ADJUDICATION_IDEMPOTENCY_KEY_INVALID",
    )


def _assert_authoritative_session(
    case: AdjudicationCaseRecord, review_session_id: str
) -> str:
    supplied = _validate_session(review_session_id)
    if supplied != case.review_session_id:
        raise AdjudicationError("ADJUDICATION_SESSION_MISMATCH")
    return case.review_session_id


async def _revalidate_case_graph(
    db: AsyncSession,
    *,
    case: AdjudicationCaseRecord,
    provider: ProviderContext,
) -> tuple[ExtractionJob, DocumentStorage]:
    """Reconstruct and verify the authoritative resource graph."""
    role = _reviewer_role(provider)
    if (
        case.contract_version != ADJUDICATION_CONTRACT_VERSION
        or case.policy_version != ADJUDICATION_POLICY_VERSION
    ):
        raise AdjudicationError("ADJUDICATION_VERSION_UNSUPPORTED")
    if (
        case.reviewer_id != provider.actor_uid
        or case.reviewer_organization_id != provider.hospital.hospital_id
        or case.tenant_id != provider.hospital.hospital_id
        or case.organization_id != provider.hospital.hospital_id
        or case.reviewer_role != role
    ):
        raise AdjudicationError("ADJUDICATION_ACCESS_DENIED")
    job = (
        await db.execute(select(ExtractionJob).where(ExtractionJob.id == case.job_id))
    ).scalar_one_or_none()
    document = (
        await db.execute(
            select(DocumentStorage).where(DocumentStorage.id == case.source_document_id)
        )
    ).scalar_one_or_none()
    if job is None or document is None:
        raise AdjudicationError("ADJUDICATION_BINDING_MISMATCH")
    if (
        job.status != "source_only"
        or job.patient_id != case.patient_id
        or job.tenant_id != case.tenant_id
        or job.document_id != case.source_document_id
        or document.patient_id != case.patient_id
        or document.tenant_id != case.tenant_id
    ):
        raise AdjudicationError("ADJUDICATION_BINDING_MISMATCH")
    if bool(case.routing_id) != bool(case.decision_id):
        raise AdjudicationError("ADJUDICATION_BINDING_MISMATCH")
    if case.routing_id is None:
        decision_count = (
            await db.execute(
                select(func.count(ExtractionDecisionRecord.id)).where(
                    ExtractionDecisionRecord.job_id == case.job_id
                )
            )
        ).scalar_one()
        if decision_count:
            raise AdjudicationError("ADJUDICATION_BINDING_MISMATCH")
    else:
        route = (
            await db.execute(
                select(ExtractionRoutingRecord).where(
                    ExtractionRoutingRecord.id == case.routing_id
                )
            )
        ).scalar_one_or_none()
        decision = (
            await db.execute(
                select(ExtractionDecisionRecord).where(
                    ExtractionDecisionRecord.id == case.decision_id
                )
            )
        ).scalar_one_or_none()
        if (
            route is None
            or decision is None
            or route.decision_id != decision.id
            or route.job_id != case.job_id
            or decision.job_id != case.job_id
            or route.patient_id != case.patient_id
            or decision.patient_id != case.patient_id
            or route.tenant_id != case.tenant_id
            or decision.tenant_id != case.tenant_id
            or route.source_document_id != case.source_document_id
            or decision.source_document_id != case.source_document_id
            or route.lane != "SOURCE_ONLY"
            or route.status != "SOURCE_RETAINED"
            or decision.lane != "SOURCE_ONLY"
            or decision.auto_commit_feature_enabled
        ):
            raise AdjudicationError("ADJUDICATION_BINDING_MISMATCH")
    return job, document


async def create_case(
    db: AsyncSession,
    *,
    provider: ProviderContext,
    idempotency_key: str,
    review_session_id: str,
    routing_id: uuid.UUID | None = None,
    job_id: uuid.UUID | None = None,
) -> AdjudicationCaseRecord:
    """Create an ordinary SOURCE_ONLY case, including honest zero-candidate cases."""
    _reviewer_role(provider)
    review_session_id = _validate_session(review_session_id)
    idempotency_key = _validate_idempotency_key(idempotency_key)
    if bool(routing_id) == bool(job_id):
        raise AdjudicationError("ADJUDICATION_SOURCE_AMBIGUOUS")

    route = decision = None
    if routing_id:
        route = (
            await db.execute(
                select(ExtractionRoutingRecord)
                .where(ExtractionRoutingRecord.id == routing_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if route is None:
            raise AdjudicationError("ADJUDICATION_ROUTE_NOT_FOUND")
        if route.lane != "SOURCE_ONLY" or route.status != "SOURCE_RETAINED":
            raise AdjudicationError("ADJUDICATION_ROUTE_INELIGIBLE")
        decision = (
            await db.execute(
                select(ExtractionDecisionRecord).where(
                    ExtractionDecisionRecord.id == route.decision_id
                )
            )
        ).scalar_one_or_none()
        job_id = route.job_id
    job = (
        await db.execute(
            select(ExtractionJob).where(ExtractionJob.id == job_id).with_for_update()
        )
    ).scalar_one_or_none()
    if job is None:
        raise AdjudicationError("ADJUDICATION_JOB_NOT_FOUND")
    document = (
        await db.execute(
            select(DocumentStorage).where(DocumentStorage.id == job.document_id)
        )
    ).scalar_one_or_none()
    if document is None:
        raise AdjudicationError("ADJUDICATION_SOURCE_NOT_FOUND")
    if route:
        if (
            decision is None
            or decision.lane != "SOURCE_ONLY"
            or decision.auto_commit_feature_enabled
            or route.job_id != decision.job_id
            or route.patient_id != decision.patient_id
            or route.tenant_id != decision.tenant_id
            or route.source_document_id != decision.source_document_id
        ):
            raise AdjudicationError("ADJUDICATION_BINDING_MISMATCH")
    else:
        decision_count = (
            await db.execute(
                select(func.count(ExtractionDecisionRecord.id)).where(
                    ExtractionDecisionRecord.job_id == job.id
                )
            )
        ).scalar_one()
        if decision_count:
            raise AdjudicationError("DOCUMENT_REVIEW_REQUIRES_ZERO_CANDIDATES")
    if (
        document.id != job.document_id
        or document.patient_id != job.patient_id
        or document.tenant_id != job.tenant_id
        or (route and route.source_document_id != document.id)
    ):
        raise AdjudicationError("ADJUDICATION_BINDING_MISMATCH")
    if job.status != "source_only":
        raise AdjudicationError("ADJUDICATION_JOB_INELIGIBLE")
    await _live_access(
        db,
        job=job,
        provider=provider,
        operation=DocumentProcessingOperation.REVIEW_EXTRACTED_FIELDS,
    )

    payload = {
        "routing_id": str(routing_id) if routing_id else None,
        "job_id": str(job.id),
        "review_session_id": review_session_id,
        "reviewer_id": provider.actor_uid,
    }
    digest = _operation_hash(payload)
    existing = (
        await db.execute(
            select(AdjudicationCaseRecord).where(
                AdjudicationCaseRecord.idempotency_key == idempotency_key
            )
        )
    ).scalar_one_or_none()
    if existing:
        if existing.operation_hash != digest:
            raise AdjudicationError("ADJUDICATION_IDEMPOTENCY_COLLISION")
        return existing
    unresolved = (
        await db.execute(
            select(AdjudicationCaseRecord).where(
                AdjudicationCaseRecord.job_id == job.id,
                AdjudicationCaseRecord.routing_id == routing_id,
                AdjudicationCaseRecord.status == "PENDING",
            )
        )
    ).scalar_one_or_none()
    if unresolved:
        raise AdjudicationError("ADJUDICATION_CASE_CONFLICT")
    now = datetime.now(timezone.utc)
    role = _reviewer_role(provider)
    case = AdjudicationCaseRecord(
        patient_id=job.patient_id,
        tenant_id=job.tenant_id,
        organization_id=job.tenant_id,
        source_document_id=document.id,
        job_id=job.id,
        routing_id=route.id if route else None,
        decision_id=decision.id if decision else None,
        reviewer_id=provider.actor_uid,
        reviewer_organization_id=provider.hospital.hospital_id,
        reviewer_role=role,
        review_session_id=review_session_id,
        status=AdjudicationOutcome.PENDING.value,
        version=1,
        idempotency_key=idempotency_key,
        operation_hash=digest,
        contract_version=ADJUDICATION_CONTRACT_VERSION,
        policy_version=ADJUDICATION_POLICY_VERSION,
        created_at=now,
    )
    db.add(case)
    await db.flush()
    await enqueue_audit_event(
        db,
        audit_context=_audit_context(case.tenant_id),
        idempotency_key=f"adjudication-case:{case.id}",
        actor_id=provider.actor_uid,
        event_type="ADJUDICATION_CASE_CREATED",
        target_id=str(case.id),
        patient_id=str(case.patient_id),
        metadata={"version": case.version, "document_level": route is None},
    )
    return case


async def submit_case(
    db: AsyncSession,
    *,
    case_id: uuid.UUID,
    provider: ProviderContext,
    review_session_id: str,
    outcome: AdjudicationOutcome,
    fields: list[dict],
    reason_codes: list[AdjudicationReasonCode | str],
    idempotency_key: str,
    supersedes_submission_id: uuid.UUID | None = None,
) -> AdjudicationSubmissionRecord:
    """Persist one immutable, canonical reviewer submission."""
    role = _reviewer_role(provider)
    _validate_idempotency_key(idempotency_key)
    case = (
        await db.execute(
            select(AdjudicationCaseRecord)
            .where(AdjudicationCaseRecord.id == case_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if case is None:
        raise AdjudicationError("ADJUDICATION_CASE_NOT_FOUND")
    authoritative_session = _assert_authoritative_session(case, review_session_id)
    if outcome in {AdjudicationOutcome.PENDING, AdjudicationOutcome.SUPERSEDED}:
        raise AdjudicationError("ADJUDICATION_OUTCOME_INVALID")
    job, _ = await _revalidate_case_graph(db, case=case, provider=provider)
    await _live_access(
        db,
        job=job,
        provider=provider,
        operation=DocumentProcessingOperation.ADJUDICATE_EXTRACTED_FIELD,
    )
    try:
        parsed = _FIELD_ADAPTER.validate_python(fields, strict=True)
        validated_reasons = _REASON_ADAPTER.validate_python(reason_codes)
    except ValidationError as exc:
        raise AdjudicationError("ADJUDICATION_PAYLOAD_INVALID") from exc
    now = datetime.now(timezone.utc)
    attempt = (
        await db.execute(
            select(func.count(AdjudicationSubmissionRecord.id)).where(
                AdjudicationSubmissionRecord.case_id == case.id
            )
        )
    ).scalar_one() + 1
    submission_id = uuid.uuid4()
    try:
        AdjudicationSubmission(
            submission_id=str(submission_id),
            case_id=str(case.id),
            patient_id=str(case.patient_id),
            tenant_id=str(case.tenant_id),
            source_document_id=str(case.source_document_id),
            job_id=str(case.job_id),
            routing_id=str(case.routing_id) if case.routing_id else None,
            decision_id=str(case.decision_id) if case.decision_id else None,
            reviewer_id=provider.actor_uid,
            reviewer_organization_id=str(provider.hospital.hospital_id),
            reviewer_role=role,
            review_session_id=authoritative_session,
            attempt_number=attempt,
            outcome=outcome,
            fields=tuple(parsed),
            supersedes_submission_id=(
                str(supersedes_submission_id) if supersedes_submission_id else None
            ),
            submitted_at=now,
            resolved_at=now,
            reason_codes=tuple(validated_reasons),
        )
    except ValidationError as exc:
        raise AdjudicationError("ADJUDICATION_PAYLOAD_INVALID") from exc
    # Exclude server-generated identifiers/timestamps so an identical caller
    # retry has an identical protected content hash.
    digest = _operation_hash(
        {
            "case_id": str(case.id),
            "patient_id": str(case.patient_id),
            "tenant_id": str(case.tenant_id),
            "source_document_id": str(case.source_document_id),
            "job_id": str(case.job_id),
            "routing_id": str(case.routing_id) if case.routing_id else None,
            "decision_id": str(case.decision_id) if case.decision_id else None,
            "reviewer_id": provider.actor_uid,
            "reviewer_organization_id": str(provider.hospital.hospital_id),
            "reviewer_role": role,
            "review_session_id": authoritative_session,
            "outcome": outcome.value,
            "fields": [item.model_dump(mode="json") for item in parsed],
            "supersedes_submission_id": (
                str(supersedes_submission_id) if supersedes_submission_id else None
            ),
            "reason_codes": [reason.value for reason in validated_reasons],
            "contract_version": ADJUDICATION_CONTRACT_VERSION,
            "policy_version": ADJUDICATION_POLICY_VERSION,
        }
    )
    existing = (
        await db.execute(
            select(AdjudicationSubmissionRecord).where(
                AdjudicationSubmissionRecord.idempotency_key == idempotency_key
            )
        )
    ).scalar_one_or_none()
    if existing:
        if existing.content_hash != digest:
            raise AdjudicationError("ADJUDICATION_IDEMPOTENCY_COLLISION")
        return existing
    if case.status != "PENDING" and supersedes_submission_id is None:
        raise AdjudicationError("ADJUDICATION_ALREADY_RESOLVED")
    if supersedes_submission_id:
        prior = (
            await db.execute(
                select(AdjudicationSubmissionRecord).where(
                    AdjudicationSubmissionRecord.id == supersedes_submission_id,
                    AdjudicationSubmissionRecord.case_id == case.id,
                )
            )
        ).scalar_one_or_none()
        if (
            prior is None
            or case.accepted_submission_id != prior.id
            or prior.outcome != "ACCEPTED"
            or prior.patient_id != case.patient_id
            or prior.tenant_id != case.tenant_id
            or prior.source_document_id != case.source_document_id
            or prior.job_id != case.job_id
            or prior.routing_id != case.routing_id
            or prior.decision_id != case.decision_id
            or prior.reviewer_id != case.reviewer_id
            or prior.reviewer_organization_id != case.reviewer_organization_id
            or prior.reviewer_role != case.reviewer_role
            or prior.review_session_id != authoritative_session
            or prior.contract_version != ADJUDICATION_CONTRACT_VERSION
            or prior.policy_version != ADJUDICATION_POLICY_VERSION
        ):
            raise AdjudicationError("ADJUDICATION_SUPERSESSION_INVALID")
        if case.clinical_committed_at is not None:
            raise AdjudicationError("ADJUDICATION_ALREADY_COMMITTED")
    row = AdjudicationSubmissionRecord(
        id=submission_id,
        case_id=case.id,
        patient_id=case.patient_id,
        tenant_id=case.tenant_id,
        source_document_id=case.source_document_id,
        job_id=case.job_id,
        routing_id=case.routing_id,
        decision_id=case.decision_id,
        reviewer_id=provider.actor_uid,
        reviewer_organization_id=provider.hospital.hospital_id,
        reviewer_role=role,
        review_session_id=authoritative_session,
        attempt_number=attempt,
        outcome=outcome.value,
        clinical_payload=[item.model_dump(mode="json") for item in parsed],
        supersedes_submission_id=supersedes_submission_id,
        submitted_at=now,
        resolved_at=now,
        contract_version=ADJUDICATION_CONTRACT_VERSION,
        policy_version=ADJUDICATION_POLICY_VERSION,
        reason_codes=[reason.value for reason in validated_reasons],
        content_hash=digest,
        idempotency_key=idempotency_key,
        created_at=now,
    )
    db.add(row)
    case.status = outcome.value
    case.version += 1
    case.resolved_at = now
    case.accepted_submission_id = (
        submission_id if outcome is AdjudicationOutcome.ACCEPTED else None
    )
    await db.flush()
    event = (
        "ADJUDICATION_SUBMISSION_SUPERSEDED"
        if supersedes_submission_id
        else {
            AdjudicationOutcome.ACCEPTED: "ADJUDICATION_SUBMISSION_ACCEPTED",
            AdjudicationOutcome.REJECTED: "ADJUDICATION_SUBMISSION_REJECTED",
            AdjudicationOutcome.NEEDS_SPECIALIST_REVIEW: (
                "ADJUDICATION_SPECIALIST_REQUESTED"
            ),
        }[outcome]
    )
    await enqueue_audit_event(
        db,
        audit_context=_audit_context(case.tenant_id),
        idempotency_key=f"adjudication-submission:{row.id}",
        actor_id=provider.actor_uid,
        event_type=event,
        target_id=str(row.id),
        patient_id=str(case.patient_id),
        metadata={
            "outcome": outcome.value,
            "field_categories": sorted({item.kind for item in parsed}),
            "reason_codes": [reason.value for reason in validated_reasons],
            "supersedes_submission_id": (
                str(supersedes_submission_id) if supersedes_submission_id else None
            ),
        },
    )
    return row


async def rotate_review_session(
    db: AsyncSession,
    *,
    case_id: uuid.UUID,
    provider: ProviderContext,
    new_review_session_id: str,
) -> AdjudicationCaseRecord:
    """Replace a lost session only for its still-pending original reviewer."""
    _reviewer_role(provider)
    new_session = _validate_session(new_review_session_id)
    case = (
        await db.execute(
            select(AdjudicationCaseRecord)
            .where(AdjudicationCaseRecord.id == case_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if case is None:
        raise AdjudicationError("ADJUDICATION_CASE_NOT_FOUND")
    if (
        case.status != AdjudicationOutcome.PENDING.value
        or case.resolved_at is not None
        or case.accepted_submission_id is not None
        or case.clinical_committed_at is not None
    ):
        raise AdjudicationError("ADJUDICATION_RECOVERY_NOT_ALLOWED")
    job, _ = await _revalidate_case_graph(db, case=case, provider=provider)
    await _live_access(
        db,
        job=job,
        provider=provider,
        operation=DocumentProcessingOperation.REVIEW_EXTRACTED_FIELDS,
    )
    case.review_session_id = new_session
    case.version += 1
    await enqueue_audit_event(
        db,
        audit_context=_audit_context(case.tenant_id),
        idempotency_key=f"adjudication-session-rotated:{case.id}:{case.version}",
        actor_id=provider.actor_uid,
        event_type="ADJUDICATION_REVIEW_SESSION_ROTATED",
        target_id=str(case.id),
        patient_id=str(case.patient_id),
        metadata={"version": case.version},
    )
    await db.flush()
    return case


async def commit_submission(
    db: AsyncSession,
    *,
    submission_id: uuid.UUID,
    provider: ProviderContext,
    review_session_id: str,
) -> AdjudicationCaseRecord:
    """Commit accepted human data exactly once in the caller transaction."""
    _reviewer_role(provider)
    submission = (
        await db.execute(
            select(AdjudicationSubmissionRecord)
            .where(AdjudicationSubmissionRecord.id == submission_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if submission is None:
        raise AdjudicationError("ADJUDICATION_SUBMISSION_NOT_FOUND")
    case = (
        await db.execute(
            select(AdjudicationCaseRecord)
            .where(AdjudicationCaseRecord.id == submission.case_id)
            .with_for_update()
        )
    ).scalar_one()
    authoritative_session = _assert_authoritative_session(case, review_session_id)
    if case.clinical_committed_at is not None:
        if case.accepted_submission_id == submission.id:
            return case
        raise AdjudicationError("ADJUDICATION_ALREADY_COMMITTED")
    if (
        submission.outcome != "ACCEPTED"
        or case.status != "ACCEPTED"
        or case.accepted_submission_id != submission.id
    ):
        raise AdjudicationError("ADJUDICATION_NOT_ACCEPTED")
    if (
        submission.contract_version != ADJUDICATION_CONTRACT_VERSION
        or submission.policy_version != ADJUDICATION_POLICY_VERSION
        or submission.reviewer_id != case.reviewer_id
        or submission.reviewer_organization_id != case.reviewer_organization_id
        or submission.reviewer_role != case.reviewer_role
        or submission.review_session_id != authoritative_session
    ):
        raise AdjudicationError("ADJUDICATION_BINDING_MISMATCH")
    bindings = (
        "patient_id",
        "tenant_id",
        "source_document_id",
        "job_id",
        "routing_id",
        "decision_id",
    )
    if any(getattr(case, key) != getattr(submission, key) for key in bindings):
        raise AdjudicationError("ADJUDICATION_BINDING_MISMATCH")
    job, _ = await _revalidate_case_graph(db, case=case, provider=provider)
    await _live_access(
        db,
        job=job,
        provider=provider,
        operation=DocumentProcessingOperation.COMMIT_VERIFIED_FIELDS,
    )
    # JSONB round-trips aware datetimes as ISO strings; Pydantic still applies
    # the same closed union and clinical validators during reconstruction.
    try:
        fields = _FIELD_ADAPTER.validate_python(submission.clinical_payload)
        reasons = _REASON_ADAPTER.validate_python(submission.reason_codes)
        AdjudicationSubmission(
            submission_id=str(submission.id),
            case_id=str(submission.case_id),
            patient_id=str(submission.patient_id),
            tenant_id=str(submission.tenant_id),
            source_document_id=str(submission.source_document_id),
            job_id=str(submission.job_id),
            routing_id=str(submission.routing_id) if submission.routing_id else None,
            decision_id=str(submission.decision_id) if submission.decision_id else None,
            reviewer_id=submission.reviewer_id,
            reviewer_organization_id=str(submission.reviewer_organization_id),
            reviewer_role=submission.reviewer_role,
            review_session_id=submission.review_session_id,
            attempt_number=submission.attempt_number,
            outcome=AdjudicationOutcome(submission.outcome),
            fields=tuple(fields),
            supersedes_submission_id=(
                str(submission.supersedes_submission_id)
                if submission.supersedes_submission_id
                else None
            ),
            submitted_at=submission.submitted_at,
            resolved_at=submission.resolved_at,
            contract_version=submission.contract_version,
            policy_version=submission.policy_version,
            reason_codes=tuple(reasons),
            content_hash=submission.content_hash,
        )
    except (ValidationError, ValueError) as exc:
        raise AdjudicationError("ADJUDICATION_PAYLOAD_INVALID") from exc
    expected_hash = _operation_hash(
        {
            "case_id": str(submission.case_id),
            "patient_id": str(submission.patient_id),
            "tenant_id": str(submission.tenant_id),
            "source_document_id": str(submission.source_document_id),
            "job_id": str(submission.job_id),
            "routing_id": (
                str(submission.routing_id) if submission.routing_id else None
            ),
            "decision_id": (
                str(submission.decision_id) if submission.decision_id else None
            ),
            "reviewer_id": submission.reviewer_id,
            "reviewer_organization_id": str(submission.reviewer_organization_id),
            "reviewer_role": submission.reviewer_role,
            "review_session_id": submission.review_session_id,
            "outcome": submission.outcome,
            "fields": [item.model_dump(mode="json") for item in fields],
            "supersedes_submission_id": (
                str(submission.supersedes_submission_id)
                if submission.supersedes_submission_id
                else None
            ),
            "reason_codes": [reason.value for reason in reasons],
            "contract_version": submission.contract_version,
            "policy_version": submission.policy_version,
        }
    )
    if submission.content_hash != expected_hash:
        raise AdjudicationError("ADJUDICATION_CONTENT_HASH_MISMATCH")
    now = datetime.now(timezone.utc)
    for field in fields:
        if isinstance(field, VitalClinicalField):
            record = Vitals(
                patient_id=case.patient_id,
                type=field.vital_type,
                value=str(field.normalized_value),
                unit=field.unit,
                recorded_at=field.effective_at,
                source="human_adjudicated",
                confidence=None,
                risk_level="LOW_RISK",
                source_document_id=case.source_document_id,
            )
            event_type = "vital_human_adjudicated"
        elif isinstance(field, LabClinicalField):
            record = LabResult(
                patient_id=case.patient_id,
                test_name=field.test_name,
                value=str(field.normalized_value),
                unit=field.unit,
                reference_range=field.reference_range,
                is_abnormal=field.is_abnormal,
                recorded_at=field.effective_at,
                source="human_adjudicated",
                confidence=None,
                risk_level="MEDIUM_RISK",
                source_document_id=case.source_document_id,
            )
            event_type = "lab_human_adjudicated"
        db.add(record)
        await db.flush()
        db.add(
            TimelineEvent(
                patient_id=case.patient_id,
                event_type=event_type,
                event_ref_id=record.id,
                occurred_at=field.effective_at,
                source="human_adjudicated",
                summary="Human-adjudicated archived document observation",
            )
        )
    case.clinical_committed_at = now
    await enqueue_audit_event(
        db,
        audit_context=_audit_context(case.tenant_id),
        idempotency_key=f"adjudication-commit:{submission.id}",
        actor_id=provider.actor_uid,
        event_type="ADJUDICATION_CLINICAL_COMMIT_COMPLETED",
        target_id=str(submission.id),
        patient_id=str(case.patient_id),
        metadata={"field_count": len(fields), "provenance": "human_adjudicated"},
    )
    await db.flush()
    return case


async def read_source_document(
    db: AsyncSession,
    *,
    case_id: uuid.UUID,
    provider: ProviderContext,
    review_session_id: str,
) -> tuple[bytes, str]:
    """Return authorized bytes directly; never expose a durable storage reference."""
    _reviewer_role(provider)
    case = (
        await db.execute(
            select(AdjudicationCaseRecord).where(AdjudicationCaseRecord.id == case_id)
        )
    ).scalar_one_or_none()
    if case is None:
        raise AdjudicationError("ADJUDICATION_CASE_NOT_FOUND")
    _assert_authoritative_session(case, review_session_id)
    job, document = await _revalidate_case_graph(db, case=case, provider=provider)
    await _live_access(
        db,
        job=job,
        provider=provider,
        operation=DocumentProcessingOperation.READ_DOCUMENT_SOURCE,
    )
    content = await get_document_storage().get_document_bytes(
        document.storage_ref,
        tenant_id=str(case.tenant_id),
        patient_id=str(case.patient_id),
    )
    await enqueue_audit_event(
        db,
        audit_context=_audit_context(case.tenant_id),
        idempotency_key=f"adjudication-source:{case.id}:{uuid.uuid4()}",
        actor_id=provider.actor_uid,
        event_type="ADJUDICATION_SOURCE_ACCESSED",
        target_id=str(case.id),
        patient_id=str(case.patient_id),
        metadata={"content_type": document.content_type},
    )
    return content, document.content_type
