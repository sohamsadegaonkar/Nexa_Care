"""Consent-bound human adjudication and clinical commit boundary."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
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
    AdjudicationConflictResolutionRecord,
    AdjudicationCaseRecord,
    AdjudicationSubmissionRecord,
    DocumentStorage,
    ExtractionDecisionRecord,
    ExtractionConflictRecord,
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
_ADJUDICATION_CASE_LOCK_VERSION = "nexa-adjudication-case:v1"
_CLINICAL_FACT_LOCK_VERSION = "nexa-clinical-fact:v1"
_C1_CLINICAL_UNIQUE_CONSTRAINTS = frozenset(
    {
        "uq_patient_vitals_human_source_fact",
        "uq_patient_lab_results_human_source_fact",
        "uq_timeline_events_human_reference",
    }
)


class AdjudicationError(RuntimeError):
    """Stable, value-free adjudication failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _is_expected_c1_unique_violation(exc: BaseException) -> bool:
    """Recognize only the C1 clinical-fact uniqueness defenses."""
    pending: list[BaseException | None] = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        diagnostic = getattr(current, "diag", None)
        sqlstate = next(
            (
                getattr(current, name, None)
                for name in ("sqlstate", "pgcode", "code")
                if getattr(current, name, None) is not None
            ),
            None,
        )
        constraint = getattr(current, "constraint_name", None) or getattr(
            diagnostic, "constraint_name", None
        )
        if sqlstate == "23505" and constraint in _C1_CLINICAL_UNIQUE_CONSTRAINTS:
            return True
        pending.extend(
            getattr(current, name, None)
            for name in ("orig", "__cause__", "__context__")
        )
    return False


def _case_lock_key(case_id: uuid.UUID) -> int:
    canonical = json.dumps(
        [_ADJUDICATION_CASE_LOCK_VERSION, str(case_id)],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode()
    return int.from_bytes(hashlib.sha256(canonical).digest()[:8], "big", signed=True)


async def _acquire_case_mutation_lock(db: AsyncSession, case_id: uuid.UUID) -> None:
    if not isinstance(db, AsyncSession):
        return
    bind = db.get_bind()
    if getattr(bind.dialect, "name", None) != "postgresql":
        return
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": _case_lock_key(case_id)},
    )


def _clinical_fact_identity(
    field: VitalClinicalField | LabClinicalField,
    *,
    patient_id: uuid.UUID,
    source_document_id: uuid.UUID,
) -> tuple[str, str, str, str, str]:
    """Return the exact, value-free identity of one authoritative observation."""
    if isinstance(field, VitalClinicalField):
        observation = field.vital_type
        kind = "VITAL"
    else:
        observation = field.test_name
        kind = "LAB_RESULT"
    return (
        kind,
        str(patient_id),
        str(source_document_id),
        observation,
        field.effective_at.isoformat(),
    )


def _clinical_fact_lock_key(identity: tuple[str, ...]) -> int:
    canonical = json.dumps(
        [_CLINICAL_FACT_LOCK_VERSION, *identity],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode()
    return int.from_bytes(hashlib.sha256(canonical).digest()[:8], "big", signed=True)


async def _acquire_clinical_fact_locks(
    db: AsyncSession, identities: set[tuple[str, ...]]
) -> None:
    """Serialize exact source-fact commits across workers in PostgreSQL."""
    if not isinstance(db, AsyncSession):
        return
    bind = db.get_bind()
    if getattr(bind.dialect, "name", None) != "postgresql":
        return
    for identity in sorted(identities):
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": _clinical_fact_lock_key(identity)},
        )


def _assert_no_duplicate_clinical_identities(
    fields: list[VitalClinicalField | LabClinicalField],
    *,
    patient_id: uuid.UUID,
    source_document_id: uuid.UUID,
) -> set[tuple[str, ...]]:
    identities = [
        _clinical_fact_identity(
            field,
            patient_id=patient_id,
            source_document_id=source_document_id,
        )
        for field in fields
    ]
    if len(identities) != len(set(identities)):
        raise AdjudicationError("ADJUDICATION_DUPLICATE_CLINICAL_FACT")
    return set(identities)


async def _existing_vital(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    source_document_id: uuid.UUID,
    field: VitalClinicalField,
) -> Vitals | None:
    return (
        await db.execute(
            select(Vitals).where(
                Vitals.patient_id == patient_id,
                Vitals.source_document_id == source_document_id,
                Vitals.type == field.vital_type,
                Vitals.recorded_at == field.effective_at,
                Vitals.source == "human_adjudicated",
            )
        )
    ).scalar_one_or_none()


async def _existing_lab(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    source_document_id: uuid.UUID,
    field: LabClinicalField,
) -> LabResult | None:
    return (
        await db.execute(
            select(LabResult).where(
                LabResult.patient_id == patient_id,
                LabResult.source_document_id == source_document_id,
                LabResult.test_name == field.test_name,
                LabResult.recorded_at == field.effective_at,
                LabResult.source == "human_adjudicated",
            )
        )
    ).scalar_one_or_none()


def _vital_content_matches(
    record: Vitals,
    field: VitalClinicalField,
    patient_id: uuid.UUID,
    source_document_id: uuid.UUID,
) -> bool:
    return (
        record.patient_id == patient_id
        and record.source_document_id == source_document_id
        and record.type == field.vital_type
        and record.value == str(field.normalized_value)
        and record.unit == field.unit
        and record.recorded_at == field.effective_at
        and record.source == "human_adjudicated"
        and record.confidence is None
        and record.risk_level == "LOW_RISK"
    )


def _lab_content_matches(
    record: LabResult,
    field: LabClinicalField,
    patient_id: uuid.UUID,
    source_document_id: uuid.UUID,
) -> bool:
    return (
        record.patient_id == patient_id
        and record.source_document_id == source_document_id
        and record.test_name == field.test_name
        and record.value == str(field.normalized_value)
        and record.unit == field.unit
        and record.reference_range == field.reference_range
        and record.is_abnormal == field.is_abnormal
        and record.recorded_at == field.effective_at
        and record.source == "human_adjudicated"
        and record.confidence is None
        and record.risk_level == "MEDIUM_RISK"
    )


async def _ensure_timeline_event(
    db: AsyncSession,
    *,
    record: Vitals | LabResult,
    event_type: str,
    effective_at: datetime,
    patient_id: uuid.UUID,
) -> bool:
    """Create one value-free timeline event, or reuse its exact existing row."""
    existing = (
        (
            await db.execute(
                select(TimelineEvent).where(
                    TimelineEvent.event_ref_id == record.id,
                    TimelineEvent.source == "human_adjudicated",
                )
            )
        )
        .scalars()
        .all()
    )
    if len(existing) > 1:
        raise AdjudicationError("ADJUDICATION_CLINICAL_FACT_COLLISION")
    if existing:
        event = existing[0]
        if not (
            event.patient_id == patient_id
            and event.event_type == event_type
            and event.event_ref_id == record.id
            and event.occurred_at == effective_at
            and event.source == "human_adjudicated"
        ):
            raise AdjudicationError("ADJUDICATION_CLINICAL_FACT_COLLISION")
        return False
    event = TimelineEvent(
        patient_id=patient_id,
        event_type=event_type,
        event_ref_id=record.id,
        occurred_at=effective_at,
        source="human_adjudicated",
        summary="Human-adjudicated archived document observation",
    )
    try:
        async with db.begin_nested():
            db.add(event)
            await db.flush()
    except IntegrityError as exc:
        if not _is_expected_c1_unique_violation(exc):
            raise
        existing = (
            (
                await db.execute(
                    select(TimelineEvent).where(
                        TimelineEvent.event_ref_id == record.id,
                        TimelineEvent.source == "human_adjudicated",
                    )
                )
            )
            .scalars()
            .all()
        )
        if len(existing) != 1:
            raise AdjudicationError("ADJUDICATION_CLINICAL_FACT_COLLISION") from exc
        event = existing[0]
        if not (
            event.patient_id == patient_id
            and event.event_type == event_type
            and event.event_ref_id == record.id
            and event.occurred_at == effective_at
            and event.source == "human_adjudicated"
        ):
            raise AdjudicationError("ADJUDICATION_CLINICAL_FACT_COLLISION") from exc
        return False
    return True


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
    resolved_conflict_ids: list[uuid.UUID] | None = None,
) -> AdjudicationSubmissionRecord:
    """Persist one immutable, canonical reviewer submission."""
    role = _reviewer_role(provider)
    _validate_idempotency_key(idempotency_key)
    await _acquire_case_mutation_lock(db, case_id)
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
    resolved_conflict_ids = sorted(
        set(resolved_conflict_ids or []), key=lambda value: value.hex
    )
    if outcome is not AdjudicationOutcome.ACCEPTED and resolved_conflict_ids:
        raise AdjudicationError("ADJUDICATION_CONFLICT_RESOLUTION_INVALID")
    if resolved_conflict_ids:
        conflicts = (
            (
                await db.execute(
                    select(ExtractionConflictRecord).where(
                        ExtractionConflictRecord.id.in_(resolved_conflict_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        if len(conflicts) != len(resolved_conflict_ids) or any(
            conflict.tenant_id != case.tenant_id
            or conflict.patient_id != case.patient_id
            or conflict.job_id != case.job_id
            or conflict.source_document_id != case.source_document_id
            for conflict in conflicts
        ):
            raise AdjudicationError("ADJUDICATION_CONFLICT_BINDING_MISMATCH")
    applicable_conflict_ids = set(
        (
            await db.execute(
                select(ExtractionConflictRecord.id).where(
                    ExtractionConflictRecord.tenant_id == case.tenant_id,
                    ExtractionConflictRecord.patient_id == case.patient_id,
                    ExtractionConflictRecord.job_id == case.job_id,
                    ExtractionConflictRecord.source_document_id
                    == case.source_document_id,
                )
            )
        ).scalars()
    )
    if outcome is AdjudicationOutcome.ACCEPTED and not applicable_conflict_ids.issubset(
        set(resolved_conflict_ids)
    ):
        raise AdjudicationError("ADJUDICATION_UNRESOLVED_CLINICAL_CONFLICT")
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
            resolved_conflict_ids=tuple(resolved_conflict_ids),
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
            "resolved_conflict_ids": [str(value) for value in resolved_conflict_ids],
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
        resolved_conflict_ids=[str(value) for value in resolved_conflict_ids],
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
    # Materialize the immutable submission before the case points at it. The
    # same-case composite FK is intentionally non-deferrable, and both flushes
    # remain inside the caller-owned transaction.
    await db.flush()
    for conflict_id in resolved_conflict_ids:
        db.add(
            AdjudicationConflictResolutionRecord(
                id=uuid.uuid4(),
                submission_id=row.id,
                conflict_id=conflict_id,
                case_id=case.id,
                created_at=now,
            )
        )
    await db.flush()
    if resolved_conflict_ids:
        await enqueue_audit_event(
            db,
            audit_context=_audit_context(case.tenant_id),
            idempotency_key=f"adjudication-conflict-resolution:{row.id}",
            actor_id=provider.actor_uid,
            event_type="ADJUDICATION_CONFLICT_RESOLUTION_ACCEPTED",
            target_id=str(row.id),
            patient_id=str(case.patient_id),
            metadata={"resolved_conflict_count": len(resolved_conflict_ids)},
        )
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
            "resolved_conflict_count": len(resolved_conflict_ids),
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
    await _acquire_case_mutation_lock(db, case_id)
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
    case_id = (
        await db.execute(
            select(AdjudicationSubmissionRecord.case_id).where(
                AdjudicationSubmissionRecord.id == submission_id
            )
        )
    ).scalar_one_or_none()
    if case_id is None:
        raise AdjudicationError("ADJUDICATION_SUBMISSION_NOT_FOUND")
    await _acquire_case_mutation_lock(db, case_id)
    case = (
        await db.execute(
            select(AdjudicationCaseRecord)
            .where(AdjudicationCaseRecord.id == case_id)
            .with_for_update()
        )
    ).scalar_one()
    submission = (
        await db.execute(
            select(AdjudicationSubmissionRecord)
            .where(AdjudicationSubmissionRecord.id == submission_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if submission is None or submission.case_id != case.id:
        raise AdjudicationError("ADJUDICATION_SUBMISSION_NOT_FOUND")
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
    applicable_conflicts = set(
        (
            await db.execute(
                select(ExtractionConflictRecord.id).where(
                    ExtractionConflictRecord.tenant_id == case.tenant_id,
                    ExtractionConflictRecord.patient_id == case.patient_id,
                    ExtractionConflictRecord.job_id == case.job_id,
                    ExtractionConflictRecord.source_document_id
                    == case.source_document_id,
                )
            )
        ).scalars()
    )
    declared_conflicts = {
        uuid.UUID(str(value)) for value in (submission.resolved_conflict_ids or [])
    }
    durable_conflicts = set(
        (
            await db.execute(
                select(AdjudicationConflictResolutionRecord.conflict_id).where(
                    AdjudicationConflictResolutionRecord.submission_id == submission.id,
                    AdjudicationConflictResolutionRecord.case_id == case.id,
                )
            )
        ).scalars()
    )
    if (
        not applicable_conflicts.issubset(declared_conflicts)
        or declared_conflicts != durable_conflicts
    ):
        raise AdjudicationError("ADJUDICATION_UNRESOLVED_CLINICAL_CONFLICT")
    # JSONB round-trips aware datetimes as ISO strings; Pydantic still applies
    # the same closed union and clinical validators during reconstruction.
    try:
        # JSONB returns ISO timestamps as JSON strings. Validate through
        # Pydantic's JSON boundary so strict models deserialize datetime values
        # without weakening the protected clinical schema.
        fields = _FIELD_ADAPTER.validate_json(json.dumps(submission.clinical_payload))
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
            resolved_conflict_ids=tuple(declared_conflicts),
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
            "resolved_conflict_ids": sorted(str(value) for value in declared_conflicts),
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
    identities = _assert_no_duplicate_clinical_identities(
        fields,
        patient_id=case.patient_id,
        source_document_id=case.source_document_id,
    )
    await _acquire_clinical_fact_locks(db, identities)
    new_field_count = 0
    reused_field_count = 0
    for field in fields:
        if isinstance(field, VitalClinicalField):
            record = await _existing_vital(
                db,
                patient_id=case.patient_id,
                source_document_id=case.source_document_id,
                field=field,
            )
            if record is not None:
                if not _vital_content_matches(
                    record, field, case.patient_id, case.source_document_id
                ):
                    raise AdjudicationError("ADJUDICATION_CLINICAL_FACT_COLLISION")
                reused_field_count += 1
            else:
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
                try:
                    async with db.begin_nested():
                        db.add(record)
                        await db.flush()
                except IntegrityError as exc:
                    if not _is_expected_c1_unique_violation(exc):
                        raise
                    record = await _existing_vital(
                        db,
                        patient_id=case.patient_id,
                        source_document_id=case.source_document_id,
                        field=field,
                    )
                    if record is None or not _vital_content_matches(
                        record, field, case.patient_id, case.source_document_id
                    ):
                        raise AdjudicationError(
                            "ADJUDICATION_CLINICAL_FACT_COLLISION"
                        ) from exc
                    reused_field_count += 1
                else:
                    new_field_count += 1
            event_type = "vital_human_adjudicated"
        else:
            record = await _existing_lab(
                db,
                patient_id=case.patient_id,
                source_document_id=case.source_document_id,
                field=field,
            )
            if record is not None:
                if not _lab_content_matches(
                    record, field, case.patient_id, case.source_document_id
                ):
                    raise AdjudicationError("ADJUDICATION_CLINICAL_FACT_COLLISION")
                reused_field_count += 1
            else:
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
                try:
                    async with db.begin_nested():
                        db.add(record)
                        await db.flush()
                except IntegrityError as exc:
                    if not _is_expected_c1_unique_violation(exc):
                        raise
                    record = await _existing_lab(
                        db,
                        patient_id=case.patient_id,
                        source_document_id=case.source_document_id,
                        field=field,
                    )
                    if record is None or not _lab_content_matches(
                        record, field, case.patient_id, case.source_document_id
                    ):
                        raise AdjudicationError(
                            "ADJUDICATION_CLINICAL_FACT_COLLISION"
                        ) from exc
                    reused_field_count += 1
                else:
                    new_field_count += 1
            event_type = "lab_human_adjudicated"
        await _ensure_timeline_event(
            db,
            record=record,
            event_type=event_type,
            effective_at=field.effective_at,
            patient_id=case.patient_id,
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
        metadata={
            "field_count": len(fields),
            "new_field_count": new_field_count,
            "reused_field_count": reused_field_count,
            "provenance": "human_adjudicated",
        },
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
