"""Metadata-only, non-release identity quarantine review workflow."""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.identity_review_gate import (
    IdentityReviewGateError,
    assert_identity_review_separation,
    authorize_identity_review,
)
from app.models.identity_review import (
    IDENTITY_REVIEW_CONTRACT_VERSION,
    REASONS_BY_OUTCOME,
    IdentityReviewCaseRecord,
    IdentityReviewCaseRouteRecord,
    IdentityReviewCaseStatus,
    IdentityReviewDispositionRecord,
    IdentityReviewMutationOperation,
    IdentityReviewOperationRecord,
    IdentityReviewOutcome,
    IdentityReviewReasonCode,
)
from app.models.pipeline import (
    DocumentStorage,
    ExtractionDecisionRecord,
    ExtractionJob,
    ExtractionRoutingRecord,
)
from app.models.provider_context import ProviderContext
from app.security.audit_context import AuditContext, AuditDomain
from app.security.identity_review_policy import (
    IDENTITY_REVIEW_POLICY_VERSION,
    IDENTITY_REVIEW_ROLE,
    IdentityReviewOperation,
)
from app.services.audit_outbox import enqueue_audit_event

_IDENTITY_JOB_ERRORS = {
    "EXTRACTED_IDENTITY_MISMATCH": IdentityReviewReasonCode.DOCUMENT_IDENTITY_MISMATCH,
    "EXTRACTED_IDENTITY_UNAVAILABLE": IdentityReviewReasonCode.CANONICAL_IDENTITY_UNAVAILABLE,
}
_IDENTITY_DECISION_REASONS = {
    "IDENTITY_MISMATCH": IdentityReviewReasonCode.DOCUMENT_IDENTITY_MISMATCH,
    "IDENTITY_UNAVAILABLE": IdentityReviewReasonCode.CANONICAL_IDENTITY_UNAVAILABLE,
}
_TERMINAL_STATES = {
    IdentityReviewCaseStatus.RESOLVED_NO_RELEASE.value,
    IdentityReviewCaseStatus.ESCALATED.value,
}


class IdentityReviewError(RuntimeError):
    """Stable, value-free identity-review failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _audit_context(tenant_id: uuid.UUID | str) -> AuditContext:
    return AuditContext.for_tenant(
        tenant_id=str(tenant_id), domain=AuditDomain.PIPELINE
    )


def _operation_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _session_binding_matches(
    stored_binding: str | None,
    current_binding: str | None,
) -> bool:
    if (
        stored_binding is None
        or current_binding is None
        or len(stored_binding) != 64
        or len(current_binding) != 64
    ):
        return False
    return secrets.compare_digest(stored_binding, current_binding)


async def _audit_access_rejected(
    db: AsyncSession,
    *,
    provider: ProviderContext,
    operation: IdentityReviewOperation,
    code: str,
    case: IdentityReviewCaseRecord | None = None,
    job: ExtractionJob | None = None,
) -> None:
    bound = case or job
    tenant_id = bound.tenant_id if bound is not None else provider.hospital.hospital_id
    patient_id = str(bound.patient_id) if bound is not None else None
    target_id = str(bound.id) if bound is not None else provider.actor_uid
    await enqueue_audit_event(
        db,
        audit_context=_audit_context(tenant_id),
        idempotency_key=f"identity-review:rejected:{uuid.uuid4()}",
        actor_id=provider.actor_uid,
        event_type="IDENTITY_REVIEW_ACCESS_REJECTED",
        target_id=target_id,
        patient_id=patient_id,
        status="REJECTED",
        metadata={"operation": operation.value, "reason": code},
    )


async def _authorize(
    db: AsyncSession,
    *,
    provider: ProviderContext,
    token: str | None,
    patient_id: uuid.UUID | str,
    tenant_id: uuid.UUID | str,
    operation: IdentityReviewOperation,
    case: IdentityReviewCaseRecord | None = None,
    job: ExtractionJob | None = None,
) -> None:
    try:
        await authorize_identity_review(
            db,
            token=token,
            patient_id=str(patient_id),
            tenant_id=str(tenant_id),
            provider=provider,
            operation=operation,
        )
        bound = case or job
        if bound is not None:
            assert_identity_review_separation(
                provider=provider,
                original_uploader_id=(
                    case.original_uploader_id if case is not None else job.uploader_id
                ),
                original_authorization_provider_id=(
                    case.original_authorization_provider_id
                    if case is not None
                    else job.authorization_provider_id
                ),
            )
    except IdentityReviewGateError as exc:
        await _audit_access_rejected(
            db,
            provider=provider,
            operation=operation,
            code=exc.code,
            case=case,
            job=job,
        )
        raise IdentityReviewError(exc.code) from exc


async def _load_job(
    db: AsyncSession, job_id: uuid.UUID, *, lock: bool
) -> ExtractionJob:
    statement = select(ExtractionJob).where(ExtractionJob.id == job_id)
    if lock:
        statement = statement.with_for_update()
    job = (await db.execute(statement)).scalar_one_or_none()
    if job is None:
        raise IdentityReviewError("IDENTITY_REVIEW_JOB_NOT_FOUND")
    return job


async def _load_case(
    db: AsyncSession,
    case_id: uuid.UUID,
    *,
    provider: ProviderContext,
    lock: bool,
) -> IdentityReviewCaseRecord:
    statement = select(IdentityReviewCaseRecord).where(
        IdentityReviewCaseRecord.id == case_id,
        IdentityReviewCaseRecord.tenant_id == provider.hospital.hospital_id,
        or_(
            and_(
                IdentityReviewCaseRecord.status
                == IdentityReviewCaseStatus.PENDING.value,
                IdentityReviewCaseRecord.assigned_reviewer_id.is_(None),
            ),
            IdentityReviewCaseRecord.assigned_reviewer_id == provider.actor_uid,
        ),
    )
    if lock:
        statement = statement.with_for_update()
    case = (await db.execute(statement)).scalar_one_or_none()
    if case is None:
        raise IdentityReviewError("IDENTITY_REVIEW_CASE_NOT_FOUND")
    if (
        case.contract_version != IDENTITY_REVIEW_CONTRACT_VERSION
        or case.policy_version != IDENTITY_REVIEW_POLICY_VERSION
    ):
        raise IdentityReviewError("IDENTITY_REVIEW_BINDING_MISMATCH")
    return case


async def _load_graph(
    db: AsyncSession,
    *,
    job: ExtractionJob,
    lock: bool,
) -> tuple[
    DocumentStorage,
    list[ExtractionRoutingRecord],
    list[ExtractionDecisionRecord],
    tuple[IdentityReviewReasonCode, ...],
]:
    document_statement = select(DocumentStorage).where(
        DocumentStorage.id == job.document_id
    )
    routes_statement = select(ExtractionRoutingRecord).where(
        ExtractionRoutingRecord.job_id == job.id
    )
    decisions_statement = select(ExtractionDecisionRecord).where(
        ExtractionDecisionRecord.job_id == job.id
    )
    if lock:
        document_statement = document_statement.with_for_update()
        routes_statement = routes_statement.with_for_update()
        decisions_statement = decisions_statement.with_for_update()
    document = (await db.execute(document_statement)).scalar_one_or_none()
    routes = list((await db.execute(routes_statement)).scalars().all())
    decisions = list((await db.execute(decisions_statement)).scalars().all())
    if (
        document is None
        or job.tenant_id is None
        or document.id != job.document_id
        or document.patient_id != job.patient_id
        or document.tenant_id != job.tenant_id
    ):
        raise IdentityReviewError("IDENTITY_REVIEW_BINDING_MISMATCH")

    decisions_by_id = {decision.id: decision for decision in decisions}
    if len(routes) != len(decisions) or len(decisions_by_id) != len(decisions):
        raise IdentityReviewError("IDENTITY_REVIEW_BINDING_MISMATCH")
    for route in routes:
        decision = decisions_by_id.get(route.decision_id)
        if (
            decision is None
            or route.job_id != job.id
            or decision.job_id != job.id
            or route.patient_id != job.patient_id
            or decision.patient_id != job.patient_id
            or route.tenant_id != job.tenant_id
            or decision.tenant_id != job.tenant_id
            or route.source_document_id != job.document_id
            or decision.source_document_id != job.document_id
            or route.lane != "QUARANTINE"
            or route.status not in {"QUARANTINE_PENDING", "QUARANTINE_ESCALATED"}
            or decision.lane != "QUARANTINE"
            or decision.auto_commit_feature_enabled
        ):
            raise IdentityReviewError("IDENTITY_REVIEW_BINDING_MISMATCH")

    reasons: set[IdentityReviewReasonCode] = set()
    job_reason = _IDENTITY_JOB_ERRORS.get(job.error_code or "")
    if job_reason is not None:
        reasons.add(job_reason)
    for decision in decisions:
        for code in decision.reason_codes:
            reason = _IDENTITY_DECISION_REASONS.get(code)
            if reason is not None:
                reasons.add(reason)
    if job.status != "quarantined" or not reasons:
        raise IdentityReviewError("IDENTITY_REVIEW_JOB_INELIGIBLE")
    return (
        document,
        routes,
        decisions,
        tuple(sorted(reasons, key=lambda item: item.value)),
    )


async def _revalidate_case_graph(
    db: AsyncSession,
    *,
    case: IdentityReviewCaseRecord,
    lock: bool,
) -> tuple[ExtractionJob, DocumentStorage]:
    job = await _load_job(db, case.job_id, lock=lock)
    document, routes, decisions, reasons = await _load_graph(db, job=job, lock=lock)
    if (
        job.patient_id != case.patient_id
        or job.tenant_id != case.tenant_id
        or job.document_id != case.source_document_id
        or document.id != case.source_document_id
        or job.uploader_id != case.original_uploader_id
        or job.authorization_provider_id != case.original_authorization_provider_id
        or job.consent_request_id != case.source_consent_request_id
        or sorted(case.identity_reason_codes)
        != sorted(reason.value for reason in reasons)
    ):
        raise IdentityReviewError("IDENTITY_REVIEW_BINDING_MISMATCH")

    bindings = list(
        (
            await db.execute(
                select(IdentityReviewCaseRouteRecord).where(
                    IdentityReviewCaseRouteRecord.case_id == case.id
                )
            )
        )
        .scalars()
        .all()
    )
    expected = {(route.id, route.decision_id) for route in routes}
    actual = {(binding.routing_id, binding.decision_id) for binding in bindings}
    if expected != actual or any(
        binding.job_id != case.job_id
        or binding.patient_id != case.patient_id
        or binding.tenant_id != case.tenant_id
        or binding.source_document_id != case.source_document_id
        for binding in bindings
    ):
        raise IdentityReviewError("IDENTITY_REVIEW_BINDING_MISMATCH")
    return job, document


async def _existing_operation(
    db: AsyncSession,
    *,
    case_id: uuid.UUID,
    operation: IdentityReviewMutationOperation,
    idempotency_key: str,
    operation_hash: str,
) -> IdentityReviewOperationRecord | None:
    row = (
        await db.execute(
            select(IdentityReviewOperationRecord).where(
                IdentityReviewOperationRecord.case_id == case_id,
                IdentityReviewOperationRecord.operation == operation.value,
                IdentityReviewOperationRecord.idempotency_key == idempotency_key,
            )
        )
    ).scalar_one_or_none()
    if row is not None and row.operation_hash != operation_hash:
        raise IdentityReviewError("IDENTITY_REVIEW_IDEMPOTENCY_COLLISION")
    return row


def _assert_session(case: IdentityReviewCaseRecord, provider: ProviderContext) -> None:
    if not provider.session_binding:
        raise IdentityReviewError("IDENTITY_REVIEW_SESSION_REQUIRED")
    if not _session_binding_matches(
        case.review_session_binding, provider.session_binding
    ):
        raise IdentityReviewError("IDENTITY_REVIEW_SESSION_MISMATCH")


async def create_case(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    provider: ProviderContext,
    capability_token: str | None,
    idempotency_key: str,
) -> IdentityReviewCaseRecord:
    job = await _load_job(db, job_id, lock=True)
    await _authorize(
        db,
        provider=provider,
        token=capability_token,
        patient_id=job.patient_id,
        tenant_id=job.tenant_id or "",
        operation=IdentityReviewOperation.CREATE_CASE,
        job=job,
    )
    document, routes, decisions, reasons = await _load_graph(db, job=job, lock=True)
    operation_hash = _operation_hash(
        {"actor_id": provider.actor_uid, "job_id": str(job.id), "operation": "CREATE"}
    )
    keyed = (
        await db.execute(
            select(IdentityReviewCaseRecord).where(
                IdentityReviewCaseRecord.tenant_id == job.tenant_id,
                IdentityReviewCaseRecord.creation_idempotency_key == idempotency_key,
            )
        )
    ).scalar_one_or_none()
    if keyed is not None:
        if keyed.creation_operation_hash != operation_hash:
            raise IdentityReviewError("IDENTITY_REVIEW_IDEMPOTENCY_COLLISION")
        return keyed
    existing = (
        await db.execute(
            select(IdentityReviewCaseRecord).where(
                IdentityReviewCaseRecord.job_id == job.id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise IdentityReviewError("IDENTITY_REVIEW_CASE_CONFLICT")

    now = datetime.now(timezone.utc)
    case = IdentityReviewCaseRecord(
        job_id=job.id,
        patient_id=job.patient_id,
        tenant_id=job.tenant_id,
        source_document_id=document.id,
        original_uploader_id=job.uploader_id,
        original_authorization_provider_id=job.authorization_provider_id,
        source_consent_request_id=job.consent_request_id,
        identity_reason_codes=[reason.value for reason in reasons],
        assigned_reviewer_id=None,
        assigned_reviewer_role=None,
        review_session_binding=None,
        status=IdentityReviewCaseStatus.PENDING.value,
        version=1,
        creation_idempotency_key=idempotency_key,
        creation_operation_hash=operation_hash,
        contract_version=IDENTITY_REVIEW_CONTRACT_VERSION,
        policy_version=IDENTITY_REVIEW_POLICY_VERSION,
        created_at=now,
        claimed_at=None,
        resolved_at=None,
    )
    db.add(case)
    await db.flush()
    for route in routes:
        db.add(
            IdentityReviewCaseRouteRecord(
                case_id=case.id,
                routing_id=route.id,
                decision_id=route.decision_id,
                job_id=job.id,
                patient_id=job.patient_id,
                tenant_id=job.tenant_id,
                source_document_id=document.id,
                created_at=now,
            )
        )
    await db.flush()
    await enqueue_audit_event(
        db,
        audit_context=_audit_context(job.tenant_id),
        idempotency_key=f"identity-review:{case.id}:created",
        actor_id=provider.actor_uid,
        event_type="IDENTITY_REVIEW_CASE_CREATED",
        target_id=str(case.id),
        patient_id=str(job.patient_id),
        metadata={
            "job_id": str(job.id),
            "route_count": len(routes),
            "reason_codes": [reason.value for reason in reasons],
            "status": case.status,
            "version": case.version,
        },
    )
    return case


async def list_cases(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    provider: ProviderContext,
    capability_token: str | None,
) -> list[IdentityReviewCaseRecord]:
    await _authorize(
        db,
        provider=provider,
        token=capability_token,
        patient_id=patient_id,
        tenant_id=provider.hospital.hospital_id,
        operation=IdentityReviewOperation.LIST_CASES,
    )
    rows = list(
        (
            await db.execute(
                select(IdentityReviewCaseRecord)
                .where(
                    IdentityReviewCaseRecord.patient_id == patient_id,
                    IdentityReviewCaseRecord.tenant_id == provider.hospital.hospital_id,
                    or_(
                        and_(
                            IdentityReviewCaseRecord.status
                            == IdentityReviewCaseStatus.PENDING.value,
                            IdentityReviewCaseRecord.assigned_reviewer_id.is_(None),
                        ),
                        IdentityReviewCaseRecord.assigned_reviewer_id
                        == provider.actor_uid,
                    ),
                    or_(
                        IdentityReviewCaseRecord.original_uploader_id.is_(None),
                        IdentityReviewCaseRecord.original_uploader_id
                        != provider.actor_uid,
                    ),
                    or_(
                        IdentityReviewCaseRecord.original_authorization_provider_id.is_(
                            None
                        ),
                        IdentityReviewCaseRecord.original_authorization_provider_id
                        != provider.actor_uid,
                    ),
                )
                .order_by(IdentityReviewCaseRecord.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    await enqueue_audit_event(
        db,
        audit_context=_audit_context(provider.hospital.hospital_id),
        idempotency_key=f"identity-review:list:{provider.actor_uid}:{uuid.uuid4()}",
        actor_id=provider.actor_uid,
        event_type="IDENTITY_REVIEW_CASE_ACCESSED",
        target_id=str(patient_id),
        patient_id=str(patient_id),
        metadata={"operation": "LIST_CASES", "case_count": len(rows)},
    )
    return rows


async def read_case(
    db: AsyncSession,
    *,
    case_id: uuid.UUID,
    provider: ProviderContext,
    capability_token: str | None,
) -> IdentityReviewCaseRecord:
    case = await _load_case(db, case_id, provider=provider, lock=False)
    await _authorize(
        db,
        provider=provider,
        token=capability_token,
        patient_id=case.patient_id,
        tenant_id=case.tenant_id,
        operation=IdentityReviewOperation.READ_CASE,
        case=case,
    )
    await _revalidate_case_graph(db, case=case, lock=False)
    await enqueue_audit_event(
        db,
        audit_context=_audit_context(case.tenant_id),
        idempotency_key=f"identity-review:{case.id}:access:{uuid.uuid4()}",
        actor_id=provider.actor_uid,
        event_type="IDENTITY_REVIEW_CASE_ACCESSED",
        target_id=str(case.id),
        patient_id=str(case.patient_id),
        metadata={"operation": "READ_CASE", "status": case.status},
    )
    return case


async def claim_case(
    db: AsyncSession,
    *,
    case_id: uuid.UUID,
    provider: ProviderContext,
    capability_token: str | None,
    expected_version: int,
    idempotency_key: str,
) -> IdentityReviewCaseRecord:
    case = await _load_case(db, case_id, provider=provider, lock=True)
    await _authorize(
        db,
        provider=provider,
        token=capability_token,
        patient_id=case.patient_id,
        tenant_id=case.tenant_id,
        operation=IdentityReviewOperation.CLAIM_CASE,
        case=case,
    )
    if not provider.session_binding:
        raise IdentityReviewError("IDENTITY_REVIEW_SESSION_REQUIRED")
    operation_hash = _operation_hash(
        {
            "actor_id": provider.actor_uid,
            "case_id": str(case.id),
            "expected_version": expected_version,
            "operation": IdentityReviewMutationOperation.CLAIM.value,
        }
    )
    prior = await _existing_operation(
        db,
        case_id=case.id,
        operation=IdentityReviewMutationOperation.CLAIM,
        idempotency_key=idempotency_key,
        operation_hash=operation_hash,
    )
    if prior is not None:
        return case
    if case.status != IdentityReviewCaseStatus.PENDING.value:
        raise IdentityReviewError("IDENTITY_REVIEW_CASE_CONFLICT")
    if case.version != expected_version:
        raise IdentityReviewError("IDENTITY_REVIEW_VERSION_CONFLICT")
    await _revalidate_case_graph(db, case=case, lock=True)
    now = datetime.now(timezone.utc)
    case.status = IdentityReviewCaseStatus.IN_REVIEW.value
    case.assigned_reviewer_id = provider.actor_uid
    case.assigned_reviewer_role = IDENTITY_REVIEW_ROLE
    case.review_session_binding = provider.session_binding
    case.claimed_at = now
    case.version += 1
    db.add(
        IdentityReviewOperationRecord(
            case_id=case.id,
            operation=IdentityReviewMutationOperation.CLAIM.value,
            actor_id=provider.actor_uid,
            actor_role=IDENTITY_REVIEW_ROLE,
            idempotency_key=idempotency_key,
            operation_hash=operation_hash,
            prior_version=expected_version,
            result_version=case.version,
            created_at=now,
        )
    )
    await db.flush()
    await enqueue_audit_event(
        db,
        audit_context=_audit_context(case.tenant_id),
        idempotency_key=f"identity-review:{case.id}:claim:{idempotency_key}",
        actor_id=provider.actor_uid,
        event_type="IDENTITY_REVIEW_CASE_CLAIMED",
        target_id=str(case.id),
        patient_id=str(case.patient_id),
        metadata={"status": case.status, "version": case.version},
    )
    return case


async def recover_session(
    db: AsyncSession,
    *,
    case_id: uuid.UUID,
    provider: ProviderContext,
    capability_token: str | None,
    expected_version: int,
    idempotency_key: str,
) -> IdentityReviewCaseRecord:
    case = await _load_case(db, case_id, provider=provider, lock=True)
    await _authorize(
        db,
        provider=provider,
        token=capability_token,
        patient_id=case.patient_id,
        tenant_id=case.tenant_id,
        operation=IdentityReviewOperation.RECOVER_SESSION,
        case=case,
    )
    if not provider.session_binding:
        raise IdentityReviewError("IDENTITY_REVIEW_SESSION_REQUIRED")
    operation_hash = _operation_hash(
        {
            "actor_id": provider.actor_uid,
            "case_id": str(case.id),
            "expected_version": expected_version,
            "operation": IdentityReviewMutationOperation.RECOVER_SESSION.value,
        }
    )
    prior = await _existing_operation(
        db,
        case_id=case.id,
        operation=IdentityReviewMutationOperation.RECOVER_SESSION,
        idempotency_key=idempotency_key,
        operation_hash=operation_hash,
    )
    if prior is not None:
        return case
    if case.status != IdentityReviewCaseStatus.IN_REVIEW.value:
        raise IdentityReviewError("IDENTITY_REVIEW_CASE_ALREADY_RESOLVED")
    if case.assigned_reviewer_id != provider.actor_uid:
        raise IdentityReviewError("IDENTITY_REVIEW_ACCESS_DENIED")
    if case.version != expected_version:
        raise IdentityReviewError("IDENTITY_REVIEW_VERSION_CONFLICT")
    if _session_binding_matches(case.review_session_binding, provider.session_binding):
        raise IdentityReviewError("IDENTITY_REVIEW_SESSION_MISMATCH")
    await _revalidate_case_graph(db, case=case, lock=True)
    now = datetime.now(timezone.utc)
    case.review_session_binding = provider.session_binding
    case.version += 1
    db.add(
        IdentityReviewOperationRecord(
            case_id=case.id,
            operation=IdentityReviewMutationOperation.RECOVER_SESSION.value,
            actor_id=provider.actor_uid,
            actor_role=IDENTITY_REVIEW_ROLE,
            idempotency_key=idempotency_key,
            operation_hash=operation_hash,
            prior_version=expected_version,
            result_version=case.version,
            created_at=now,
        )
    )
    await db.flush()
    await enqueue_audit_event(
        db,
        audit_context=_audit_context(case.tenant_id),
        idempotency_key=f"identity-review:{case.id}:session:{idempotency_key}",
        actor_id=provider.actor_uid,
        event_type="IDENTITY_REVIEW_SESSION_ROTATED",
        target_id=str(case.id),
        patient_id=str(case.patient_id),
        metadata={"status": case.status, "version": case.version},
    )
    return case


async def submit_disposition(
    db: AsyncSession,
    *,
    case_id: uuid.UUID,
    provider: ProviderContext,
    capability_token: str | None,
    expected_version: int,
    idempotency_key: str,
    outcome: IdentityReviewOutcome,
    reason_codes: tuple[IdentityReviewReasonCode, ...],
) -> IdentityReviewDispositionRecord:
    if not reason_codes or len(set(reason_codes)) != len(reason_codes):
        raise IdentityReviewError("IDENTITY_REVIEW_PAYLOAD_INVALID")
    if any(reason not in REASONS_BY_OUTCOME[outcome] for reason in reason_codes):
        raise IdentityReviewError("IDENTITY_REVIEW_PAYLOAD_INVALID")
    case = await _load_case(db, case_id, provider=provider, lock=True)
    await _authorize(
        db,
        provider=provider,
        token=capability_token,
        patient_id=case.patient_id,
        tenant_id=case.tenant_id,
        operation=IdentityReviewOperation.SUBMIT_DISPOSITION,
        case=case,
    )
    operation_hash = _operation_hash(
        {
            "actor_id": provider.actor_uid,
            "case_id": str(case.id),
            "expected_version": expected_version,
            "operation": IdentityReviewMutationOperation.SUBMIT_DISPOSITION.value,
            "outcome": outcome.value,
            "reason_codes": sorted(reason.value for reason in reason_codes),
        }
    )
    prior = await _existing_operation(
        db,
        case_id=case.id,
        operation=IdentityReviewMutationOperation.SUBMIT_DISPOSITION,
        idempotency_key=idempotency_key,
        operation_hash=operation_hash,
    )
    if prior is not None:
        disposition = (
            await db.execute(
                select(IdentityReviewDispositionRecord).where(
                    IdentityReviewDispositionRecord.case_id == case.id
                )
            )
        ).scalar_one_or_none()
        if disposition is None:
            raise IdentityReviewError("IDENTITY_REVIEW_BINDING_MISMATCH")
        return disposition
    if case.status in _TERMINAL_STATES:
        raise IdentityReviewError("IDENTITY_REVIEW_CASE_ALREADY_RESOLVED")
    if case.status != IdentityReviewCaseStatus.IN_REVIEW.value:
        raise IdentityReviewError("IDENTITY_REVIEW_CASE_CONFLICT")
    if case.assigned_reviewer_id != provider.actor_uid:
        raise IdentityReviewError("IDENTITY_REVIEW_ACCESS_DENIED")
    _assert_session(case, provider)
    if case.version != expected_version:
        raise IdentityReviewError("IDENTITY_REVIEW_VERSION_CONFLICT")
    job, document = await _revalidate_case_graph(db, case=case, lock=True)
    if job.status != "quarantined" or document.patient_id != case.patient_id:
        raise IdentityReviewError("IDENTITY_REVIEW_BINDING_MISMATCH")

    now = datetime.now(timezone.utc)
    disposition = IdentityReviewDispositionRecord(
        case_id=case.id,
        patient_id=case.patient_id,
        tenant_id=case.tenant_id,
        reviewer_id=provider.actor_uid,
        reviewer_role=IDENTITY_REVIEW_ROLE,
        outcome=outcome.value,
        reason_codes=[reason.value for reason in reason_codes],
        prior_case_version=expected_version,
        idempotency_key=idempotency_key,
        operation_hash=operation_hash,
        contract_version=IDENTITY_REVIEW_CONTRACT_VERSION,
        policy_version=IDENTITY_REVIEW_POLICY_VERSION,
        submitted_at=now,
    )
    case.status = (
        IdentityReviewCaseStatus.ESCALATED.value
        if outcome is IdentityReviewOutcome.SECURITY_ESCALATION_REQUIRED
        else IdentityReviewCaseStatus.RESOLVED_NO_RELEASE.value
    )
    case.resolved_at = now
    case.version += 1
    db.add(disposition)
    db.add(
        IdentityReviewOperationRecord(
            case_id=case.id,
            operation=IdentityReviewMutationOperation.SUBMIT_DISPOSITION.value,
            actor_id=provider.actor_uid,
            actor_role=IDENTITY_REVIEW_ROLE,
            idempotency_key=idempotency_key,
            operation_hash=operation_hash,
            prior_version=expected_version,
            result_version=case.version,
            created_at=now,
        )
    )
    await db.flush()
    safe_metadata = {
        "outcome": outcome.value,
        "reason_codes": [reason.value for reason in reason_codes],
        "status": case.status,
        "version": case.version,
    }
    await enqueue_audit_event(
        db,
        audit_context=_audit_context(case.tenant_id),
        idempotency_key=f"identity-review:{case.id}:disposition:{idempotency_key}",
        actor_id=provider.actor_uid,
        event_type="IDENTITY_REVIEW_DISPOSITION_SUBMITTED",
        target_id=str(case.id),
        patient_id=str(case.patient_id),
        metadata=safe_metadata,
    )
    if outcome is IdentityReviewOutcome.SECURITY_ESCALATION_REQUIRED:
        await enqueue_audit_event(
            db,
            audit_context=_audit_context(case.tenant_id),
            idempotency_key=f"identity-review:{case.id}:escalated:{idempotency_key}",
            actor_id=provider.actor_uid,
            event_type="IDENTITY_REVIEW_ESCALATED",
            target_id=str(case.id),
            patient_id=str(case.patient_id),
            metadata=safe_metadata,
        )
    return disposition


async def case_metadata(
    db: AsyncSession,
    *,
    case: IdentityReviewCaseRecord,
    provider: ProviderContext,
) -> dict[str, Any]:
    route_count = (
        await db.execute(
            select(func.count(IdentityReviewCaseRouteRecord.id)).where(
                IdentityReviewCaseRouteRecord.case_id == case.id
            )
        )
    ).scalar_one()
    return {
        "case_id": str(case.id),
        "job_id": str(case.job_id),
        "patient_id": str(case.patient_id),
        "tenant_id": str(case.tenant_id),
        "document_id": str(case.source_document_id),
        "status": case.status,
        "identity_reason_codes": list(case.identity_reason_codes),
        "is_assigned": case.assigned_reviewer_id is not None,
        "assigned_to_current_reviewer": (
            case.assigned_reviewer_id == provider.actor_uid
        ),
        "version": case.version,
        "created_at": case.created_at,
        "claimed_at": case.claimed_at,
        "resolved_at": case.resolved_at,
        "route_count": int(route_count),
        "contract_version": case.contract_version,
        "policy_version": case.policy_version,
    }


__all__ = [
    "IdentityReviewError",
    "case_metadata",
    "claim_case",
    "create_case",
    "list_cases",
    "read_case",
    "recover_session",
    "submit_disposition",
]
