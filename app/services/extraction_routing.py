"""Transactional persistence and operational routing for extraction decisions."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.extraction_decision import (
    DECISION_CONTRACT_VERSION,
    DECISION_EVALUATOR_VERSION,
    DECISION_POLICY_VERSION,
    DecisionLane,
    ExtractionDecision,
    ExtractionDecisionPolicy,
)
from app.models.field_evidence import ExtractedFieldEvidence
from app.models.pipeline import (
    ExtractionDecisionRecord,
    ExtractionJob,
    ExtractionRoutingRecord,
)
from app.security.audit_context import AuditContext
from app.services.audit_outbox import enqueue_audit_event
from app.services.extraction_decision_engine import evaluate_extraction_evidence

SOURCE_RETAINED = "SOURCE_RETAINED"
QUARANTINE_PENDING = "QUARANTINE_PENDING"
QUARANTINE_ESCALATED = "QUARANTINE_ESCALATED"


class ExtractionRoutingError(RuntimeError):
    """Stable, non-sensitive routing boundary failure."""


class ExtractionRoutingCollision(ExtractionRoutingError):
    pass


@dataclass(frozen=True, slots=True)
class DurableRoutingResult:
    decision: ExtractionDecisionRecord
    routing: ExtractionRoutingRecord
    idempotent: bool


def _uuid(value: str, code: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ExtractionRoutingError(code) from exc


def _operation_hash(decision: ExtractionDecision) -> str:
    safe_projection = {
        "decision_id": decision.decision_id,
        "evidence_id": decision.evidence_id,
        "job_id": decision.job_id,
        "attempt_id": decision.attempt_id,
        "patient_id": decision.patient_id,
        "tenant_id": decision.tenant_id,
        "source_document_id": decision.source_document_id,
        "lane": decision.lane.value,
        "reasons": [reason.value for reason in decision.reasons],
        "policy_hash": decision.policy_configuration_hash,
        "evidence_digest": decision.evidence_digest,
    }
    encoded = json.dumps(
        safe_projection, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _revalidate_decision(decision: ExtractionDecision) -> ExtractionDecision:
    try:
        validated = ExtractionDecision.model_validate_json(decision.model_dump_json())
    except (AttributeError, TypeError, ValueError, ValidationError) as exc:
        raise ExtractionRoutingError("DECISION_INPUT_INVALID") from exc
    if (
        validated.decision_contract_version != DECISION_CONTRACT_VERSION
        or validated.policy_version != DECISION_POLICY_VERSION
        or validated.evaluator_version != DECISION_EVALUATOR_VERSION
    ):
        raise ExtractionRoutingError("DECISION_VERSION_UNSUPPORTED")
    if validated.lane is DecisionLane.AUTO_COMMIT:
        raise ExtractionRoutingError("RUNTIME_AUTO_COMMIT_FORBIDDEN")
    if validated.auto_commit_feature_enabled:
        raise ExtractionRoutingError("RUNTIME_AUTO_COMMIT_FORBIDDEN")
    return validated


def _assert_job_binding(decision: ExtractionDecision, job: ExtractionJob) -> None:
    expected = (
        (decision.job_id, str(job.id)),
        (decision.patient_id, str(job.patient_id)),
        (decision.tenant_id, str(job.tenant_id)),
        (decision.organization_id, str(job.tenant_id)),
        (decision.source_document_id, str(job.document_id)),
        (decision.workflow_id, str(job.consent_request_id)),
        (decision.request_id, str(job.request_id)),
    )
    if any(actual != bound for actual, bound in expected):
        raise ExtractionRoutingError("DECISION_JOB_BINDING_MISMATCH")
    if decision.attempt_id != f"{job.id}:{job.attempt_count}":
        raise ExtractionRoutingError("DECISION_ATTEMPT_BINDING_MISMATCH")


async def persist_lane_decision(
    db: AsyncSession,
    *,
    decision: ExtractionDecision,
    job: ExtractionJob,
    audit_context: AuditContext,
    actor_id: str,
    idempotency_key: str,
    created_at: datetime,
    quarantine_review_deadline: datetime | None,
) -> DurableRoutingResult:
    """Stage one append-only decision, route, and audit event without commit."""
    validated = _revalidate_decision(decision)
    _assert_job_binding(validated, job)
    operation_hash = _operation_hash(validated)

    existing_route = (
        await db.execute(
            select(ExtractionRoutingRecord)
            .where(ExtractionRoutingRecord.idempotency_key == idempotency_key)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if existing_route is not None:
        if existing_route.operation_hash != operation_hash:
            raise ExtractionRoutingCollision("ROUTING_IDEMPOTENCY_COLLISION")
        existing_decision = (
            await db.execute(
                select(ExtractionDecisionRecord).where(
                    ExtractionDecisionRecord.id == existing_route.decision_id
                )
            )
        ).scalar_one()
        return DurableRoutingResult(existing_decision, existing_route, True)

    if validated.lane is DecisionLane.QUARANTINE:
        if quarantine_review_deadline is None:
            raise ExtractionRoutingError("QUARANTINE_DEADLINE_REQUIRED")
        route_status = QUARANTINE_PENDING
    else:
        if quarantine_review_deadline is not None:
            raise ExtractionRoutingError("SOURCE_ONLY_DEADLINE_FORBIDDEN")
        route_status = SOURCE_RETAINED

    decision_row = ExtractionDecisionRecord(
        id=_uuid(validated.decision_id, "DECISION_ID_INVALID"),
        decision_contract_version=validated.decision_contract_version,
        evidence_contract_version=validated.evidence_contract_version,
        evidence_id=_uuid(validated.evidence_id, "EVIDENCE_ID_INVALID"),
        patient_id=_uuid(validated.patient_id, "PATIENT_ID_INVALID"),
        tenant_id=_uuid(validated.tenant_id, "TENANT_ID_INVALID"),
        organization_id=_uuid(validated.organization_id, "ORGANIZATION_ID_INVALID"),
        source_document_id=_uuid(
            validated.source_document_id, "SOURCE_DOCUMENT_ID_INVALID"
        ),
        job_id=_uuid(validated.job_id, "JOB_ID_INVALID"),
        workflow_id=validated.workflow_id,
        request_id=validated.request_id,
        attempt_id=validated.attempt_id,
        lane=validated.lane.value,
        reason_codes=[reason.value for reason in validated.reasons],
        policy_version=validated.policy_version,
        policy_configuration_hash=validated.policy_configuration_hash,
        evidence_digest=validated.evidence_digest,
        evaluated_at=validated.evaluated_at,
        evaluator_version=validated.evaluator_version,
        auto_commit_feature_enabled=False,
        earlier_decision_id=(
            _uuid(validated.earlier_decision_id, "EARLIER_DECISION_ID_INVALID")
            if validated.earlier_decision_id
            else None
        ),
        created_at=created_at,
    )
    route_row = ExtractionRoutingRecord(
        decision_id=decision_row.id,
        job_id=decision_row.job_id,
        patient_id=decision_row.patient_id,
        tenant_id=decision_row.tenant_id,
        source_document_id=decision_row.source_document_id,
        lane=validated.lane.value,
        status=route_status,
        routed_at=created_at,
        quarantine_review_deadline=quarantine_review_deadline,
        idempotency_key=idempotency_key,
        operation_hash=operation_hash,
        created_at=created_at,
    )
    db.add(decision_row)
    db.add(route_row)
    await db.flush()
    await enqueue_audit_event(
        db,
        audit_context=audit_context,
        idempotency_key=f"{idempotency_key}:audit",
        actor_id=actor_id,
        event_type="EXTRACTION_EVIDENCE_ROUTED",
        target_id=str(decision_row.id),
        patient_id=str(decision_row.patient_id),
        metadata={
            "job_id": str(decision_row.job_id),
            "document_id": str(decision_row.source_document_id),
            "decision_id": str(decision_row.id),
            "lane": validated.lane.value,
            "reason_codes": [reason.value for reason in validated.reasons],
            "policy_version": validated.policy_version,
            "evaluator_version": validated.evaluator_version,
        },
    )
    return DurableRoutingResult(decision_row, route_row, False)


async def evaluate_and_persist_lane(
    db: AsyncSession,
    *,
    evidence: ExtractedFieldEvidence,
    policy: ExtractionDecisionPolicy,
    job: ExtractionJob,
    audit_context: AuditContext,
    actor_id: str,
    evaluated_at: datetime,
    quarantine_review_deadline: datetime | None = None,
    earlier_decision_id: str | None = None,
) -> DurableRoutingResult:
    """Pure evaluation followed by caller-owned transactional persistence."""
    decision = evaluate_extraction_evidence(
        evidence=evidence,
        policy=policy,
        decision_id_factory=lambda: str(uuid.uuid4()),
        evaluated_at=evaluated_at,
        earlier_decision_id=earlier_decision_id,
    )
    if decision.lane is DecisionLane.QUARANTINE and quarantine_review_deadline is None:
        # No unapproved retention duration is invented. A fail-closed item is
        # immediately eligible for the separately invoked escalation boundary.
        quarantine_review_deadline = evaluated_at
    return await persist_lane_decision(
        db,
        decision=decision,
        job=job,
        audit_context=audit_context,
        actor_id=actor_id,
        idempotency_key=(
            f"extraction-route:{job.id}:{job.attempt_count}:{evidence.evidence_id}"
        ),
        created_at=evaluated_at,
        quarantine_review_deadline=quarantine_review_deadline,
    )


async def escalate_expired_quarantine(
    db: AsyncSession,
    *,
    routing_id: uuid.UUID,
    now: datetime,
    audit_context: AuditContext,
    actor_id: str,
) -> ExtractionRoutingRecord:
    """Stage the idempotent pending-to-escalated transition and audit."""
    route = (
        await db.execute(
            select(ExtractionRoutingRecord)
            .where(ExtractionRoutingRecord.id == routing_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if route is None:
        raise ExtractionRoutingError("ROUTING_ITEM_NOT_FOUND")
    if route.status == QUARANTINE_ESCALATED:
        return route
    if (
        route.status != QUARANTINE_PENDING
        or route.lane != DecisionLane.QUARANTINE.value
        or route.quarantine_review_deadline is None
        or route.quarantine_review_deadline > now
    ):
        raise ExtractionRoutingError("QUARANTINE_NOT_EXPIRED")
    route.status = QUARANTINE_ESCALATED
    route.escalated_at = now
    await enqueue_audit_event(
        db,
        audit_context=audit_context,
        idempotency_key=f"extraction-route:{route.id}:escalated",
        actor_id=actor_id,
        event_type="EXTRACTION_QUARANTINE_ESCALATED",
        target_id=str(route.id),
        patient_id=str(route.patient_id),
        metadata={
            "decision_id": str(route.decision_id),
            "job_id": str(route.job_id),
            "lane": route.lane,
            "status": route.status,
        },
    )
    await db.flush()
    return route
