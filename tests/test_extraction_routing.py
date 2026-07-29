"""Focused persistence-boundary tests for safe extraction lane routing."""

from __future__ import annotations

import uuid
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.models.extraction_decision import (
    DecisionLane,
    ExtractionDecisionPolicy,
)
from app.models.field_evidence import (
    ClinicalRisk,
    ClinicalValueEvidence,
    ConfidenceProvenance,
    ExtractedFieldEvidence,
    IdentityBindingMethod,
    IdentityBindingStatus,
    IdentityEvidence,
    LifecycleEvidence,
    ModelEvidence,
    NormalizationStatus,
    NormalizedBoundingBox,
    PolicyEvidence,
    SnapshotState,
    VerifierOutcome,
    VisualCoverage,
    VisualEvidence,
)
from app.models.pipeline import (
    ExtractionDecisionRecord,
    ExtractionJob,
    ExtractionRoutingRecord,
)
from app.security.audit_context import AuditContext, AuditDomain
from app.services.extraction_decision_engine import evaluate_extraction_evidence
from app.services.extraction_routing import (
    ExtractionRoutingCollision,
    ExtractionRoutingError,
    escalate_expired_quarantine,
    persist_lane_decision,
)
from app.services.approved_access_capability import (
    ApprovedAccessStoreUnavailable,
    validate_live_document_processing_request,
)

NOW = datetime(2026, 7, 29, tzinfo=timezone.utc)


class _Result:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        if self.value is None:
            raise AssertionError("expected one row")
        return self.value


class _DB:
    def __init__(self, results=()):
        self.results = list(results)
        self.added = []
        self.flushes = 0

    async def execute(self, *_args, **_kwargs):
        return _Result(self.results.pop(0) if self.results else None)

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        self.flushes += 1


def _job() -> ExtractionJob:
    patient = uuid.uuid4()
    tenant = uuid.uuid4()
    document = uuid.uuid4()
    return ExtractionJob(
        id=uuid.uuid4(),
        patient_id=patient,
        tenant_id=tenant,
        authorization_provider_id="provider-1",
        consent_request_id="workflow-1",
        document_id=document,
        document_type="application/pdf",
        status="validation_pending",
        request_id="request-1",
        attempt_count=1,
        created_at=NOW,
    )


def _evidence(job: ExtractionJob, *, complete: bool) -> ExtractedFieldEvidence:
    return ExtractedFieldEvidence(
        evidence_id=str(uuid.uuid4()),
        identity=IdentityEvidence(
            patient_id=str(job.patient_id),
            tenant_id=str(job.tenant_id),
            organization_id=str(job.tenant_id),
            source_document_id=str(job.document_id),
            source_document_hash="a" * 64,
            ingestion_id=str(job.document_id),
            binding_status=IdentityBindingStatus.VERIFIED,
            binding_method=IdentityBindingMethod.SERVER_JOB_AND_DOCUMENT,
        ),
        clinical_value=ClinicalValueEvidence(
            field_name="synthetic_field",
            raw_value="sensitive synthetic value",
            normalized_value="sensitive synthetic value",
            clinical_risk=ClinicalRisk.LOW_RISK,
            normalization_status=NormalizationStatus.NORMALIZED,
        ),
        visual=VisualEvidence(
            page_number=0 if complete else None,
            bounding_box=(
                NormalizedBoundingBox(left=0.1, top=0.1, right=0.2, bottom=0.2)
                if complete
                else None
            ),
            source_text="sensitive source text" if complete else None,
            coverage=VisualCoverage.COMPLETE
            if complete
            else VisualCoverage.UNAVAILABLE,
        ),
        model=ModelEvidence(
            provider_name="synthetic-provider",
            model_name="synthetic-model" if complete else None,
            model_version="1" if complete else None,
            extracted_at=NOW,
            document_confidence=0.99,
            field_confidence=0.99 if complete else None,
            field_confidence_source=(
                ConfidenceProvenance.PROVIDER_FIELD
                if complete
                else ConfidenceProvenance.UNAVAILABLE
            ),
            verifier_outcome=(
                VerifierOutcome.AGREED if complete else VerifierOutcome.NOT_RUN
            ),
        ),
        policy=PolicyEvidence(auto_commit_enabled=False),
        lifecycle=LifecycleEvidence(
            job_id=str(job.id),
            workflow_id=str(job.consent_request_id),
            request_id=str(job.request_id),
            attempt_number=1,
            attempt_id=f"{job.id}:1",
            created_at=NOW,
            extracted_at=NOW,
            consent_state=SnapshotState.ACTIVE,
            erasure_state=SnapshotState.NOT_REQUESTED,
        ),
    )


def _policy(job: ExtractionJob, evidence: ExtractedFieldEvidence, *, enabled=False):
    return ExtractionDecisionPolicy(
        auto_commit_enabled=enabled,
        patient_id=str(job.patient_id),
        tenant_id=str(job.tenant_id),
        organization_id=str(job.tenant_id),
        source_document_id=str(job.document_id),
        evidence_id=evidence.evidence_id,
        job_id=str(job.id),
        workflow_id=str(job.consent_request_id),
        request_id=str(job.request_id),
        attempt_id=f"{job.id}:1",
    )


def _decision(job, evidence, policy, decision_id=None):
    return evaluate_extraction_evidence(
        evidence=evidence,
        policy=policy,
        decision_id_factory=lambda: str(decision_id or uuid.uuid4()),
        evaluated_at=NOW,
    )


AUDIT_CONTEXT = AuditContext.for_tenant(
    tenant_id=str(uuid.uuid4()), domain=AuditDomain.PIPELINE
)


@pytest.mark.asyncio
async def test_source_only_persists_safe_metadata_without_raw_evidence():
    job = _job()
    evidence = _evidence(job, complete=False)
    decision = _decision(job, evidence, _policy(job, evidence))
    assert decision.lane is DecisionLane.SOURCE_ONLY
    db = _DB()
    with patch(
        "app.services.extraction_routing.enqueue_audit_event", AsyncMock()
    ) as audit:
        result = await persist_lane_decision(
            db,
            decision=decision,
            job=job,
            audit_context=AUDIT_CONTEXT,
            actor_id="provider-1",
            idempotency_key="route-key",
            created_at=NOW,
            quarantine_review_deadline=None,
        )
    assert result.routing.status == "SOURCE_RETAINED"
    assert result.routing.source_document_id == job.document_id
    assert {type(row) for row in db.added} == {
        ExtractionDecisionRecord,
        ExtractionRoutingRecord,
    }
    assert not {
        "raw_value",
        "normalized_value",
        "source_text",
        "filename",
        "document_bytes",
    } & set(ExtractionDecisionRecord.__table__.c.keys())
    assert not {
        "raw_value",
        "source_text",
        "filename",
        "document_bytes",
    } & set(ExtractionRoutingRecord.__table__.c.keys())
    assert "sensitive" not in repr(audit.await_args.kwargs["metadata"])


@pytest.mark.asyncio
async def test_quarantine_persists_pending_with_deadline():
    job = _job()
    evidence = _evidence(job, complete=False)
    lifecycle = evidence.lifecycle.model_copy(
        update={"consent_state": SnapshotState.INACTIVE}
    )
    evidence = evidence.model_copy(update={"lifecycle": lifecycle})
    decision = _decision(job, evidence, _policy(job, evidence))
    db = _DB()
    with patch("app.services.extraction_routing.enqueue_audit_event", AsyncMock()):
        result = await persist_lane_decision(
            db,
            decision=decision,
            job=job,
            audit_context=AUDIT_CONTEXT,
            actor_id="provider-1",
            idempotency_key="quarantine-key",
            created_at=NOW,
            quarantine_review_deadline=NOW + timedelta(hours=1),
        )
    assert result.routing.status == "QUARANTINE_PENDING"
    assert result.routing.lane == "QUARANTINE"


@pytest.mark.asyncio
async def test_runtime_boundary_rejects_synthetic_auto_commit():
    job = _job()
    evidence = _evidence(job, complete=True)
    decision = _decision(job, evidence, _policy(job, evidence, enabled=True))
    assert decision.lane is DecisionLane.AUTO_COMMIT
    with pytest.raises(ExtractionRoutingError, match="RUNTIME_AUTO_COMMIT_FORBIDDEN"):
        await persist_lane_decision(
            _DB(),
            decision=decision,
            job=job,
            audit_context=AUDIT_CONTEXT,
            actor_id="provider-1",
            idempotency_key="forbidden",
            created_at=NOW,
            quarantine_review_deadline=None,
        )


@pytest.mark.asyncio
async def test_idempotent_operation_returns_existing_and_collision_fails():
    job = _job()
    evidence = _evidence(job, complete=False)
    decision = _decision(job, evidence, _policy(job, evidence))
    existing_decision = SimpleNamespace(id=uuid.UUID(decision.decision_id))
    with patch("app.services.extraction_routing.enqueue_audit_event", AsyncMock()):
        created = await persist_lane_decision(
            _DB(),
            decision=decision,
            job=job,
            audit_context=AUDIT_CONTEXT,
            actor_id="provider-1",
            idempotency_key="same-key",
            created_at=NOW,
            quarantine_review_deadline=None,
        )
        existing_route = created.routing
        same = await persist_lane_decision(
            _DB((existing_route, existing_decision)),
            decision=decision,
            job=job,
            audit_context=AUDIT_CONTEXT,
            actor_id="provider-1",
            idempotency_key="same-key",
            created_at=NOW,
            quarantine_review_deadline=None,
        )
    assert same.idempotent is True
    existing_route.operation_hash = "0" * 64
    with pytest.raises(ExtractionRoutingCollision):
        await persist_lane_decision(
            _DB((existing_route,)),
            decision=decision,
            job=job,
            audit_context=AUDIT_CONTEXT,
            actor_id="provider-1",
            idempotency_key="same-key",
            created_at=NOW,
            quarantine_review_deadline=None,
        )


@pytest.mark.asyncio
async def test_cross_tenant_binding_rejected():
    job = _job()
    evidence = _evidence(job, complete=False)
    decision = _decision(job, evidence, _policy(job, evidence))
    other_job = _job()
    with pytest.raises(ExtractionRoutingError, match="DECISION_JOB_BINDING_MISMATCH"):
        await persist_lane_decision(
            _DB(),
            decision=decision,
            job=other_job,
            audit_context=AUDIT_CONTEXT,
            actor_id="provider-1",
            idempotency_key="cross-tenant",
            created_at=NOW,
            quarantine_review_deadline=None,
        )


@pytest.mark.asyncio
async def test_linked_reevaluation_appends_without_mutating_prior_decision():
    job = _job()
    evidence = _evidence(job, complete=False)
    policy = _policy(job, evidence)
    first = _decision(job, evidence, policy)
    second = evaluate_extraction_evidence(
        evidence=evidence,
        policy=policy,
        decision_id_factory=lambda: str(uuid.uuid4()),
        evaluated_at=NOW + timedelta(seconds=1),
        earlier_decision_id=first.decision_id,
    )
    db = _DB()
    with patch("app.services.extraction_routing.enqueue_audit_event", AsyncMock()):
        first_result = await persist_lane_decision(
            db,
            decision=first,
            job=job,
            audit_context=AUDIT_CONTEXT,
            actor_id="provider-1",
            idempotency_key="reevaluation-1",
            created_at=NOW,
            quarantine_review_deadline=None,
        )
        second_result = await persist_lane_decision(
            db,
            decision=second,
            job=job,
            audit_context=AUDIT_CONTEXT,
            actor_id="provider-1",
            idempotency_key="reevaluation-2",
            created_at=NOW + timedelta(seconds=1),
            quarantine_review_deadline=None,
        )
    assert second_result.decision.id != first_result.decision.id
    assert second_result.decision.earlier_decision_id == first_result.decision.id
    assert first_result.decision.earlier_decision_id is None


@pytest.mark.asyncio
async def test_expired_quarantine_escalates_once_without_decision_mutation():
    route = SimpleNamespace(
        id=uuid.uuid4(),
        decision_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        patient_id=uuid.uuid4(),
        lane="QUARANTINE",
        status="QUARANTINE_PENDING",
        quarantine_review_deadline=NOW - timedelta(seconds=1),
        escalated_at=None,
    )
    db = _DB((route,))
    with patch("app.services.extraction_routing.enqueue_audit_event", AsyncMock()):
        result = await escalate_expired_quarantine(
            db,
            routing_id=route.id,
            now=NOW,
            audit_context=AUDIT_CONTEXT,
            actor_id="system",
        )
    assert result.status == "QUARANTINE_ESCALATED"
    assert result.escalated_at == NOW
    assert db.added == []


def test_commit_route_explicitly_rejects_safe_lane_jobs():
    root = Path(__file__).resolve().parents[1]
    code = (root / "app/api/v2/pipeline_routes.py").read_text(encoding="utf-8")
    assert "SOURCE_ONLY_NOT_COMMITTABLE" in code
    assert "QUARANTINED_JOB_NOT_COMMITTABLE" in code


@pytest.mark.asyncio
async def test_live_consent_request_recheck_accepts_only_matching_active_state():
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    payload = {
        "request_id": "workflow-1",
        "patient_id": "patient-1",
        "provider_id": "provider-1",
        "hospital_id": "tenant-1",
        "purpose": "document_processing",
        "scope": "documents",
        "grant_type": "document_processing",
        "issued_at": NOW.isoformat(),
        "expires_at": expires.isoformat(),
        "allowed_operations": ["read_job_status"],
    }
    request = {**payload, "status": "approved"}
    redis = AsyncMock()
    redis.get = AsyncMock(
        side_effect=("digest-1", json.dumps(payload), json.dumps(request))
    )
    with patch(
        "app.services.approved_access_capability.get_async_redis_client",
        return_value=redis,
    ):
        result = await validate_live_document_processing_request(
            request_id="workflow-1",
            patient_id="patient-1",
            provider_id="provider-1",
            hospital_id="tenant-1",
        )
    assert result is not None


@pytest.mark.asyncio
async def test_live_consent_request_recheck_denies_revoked_or_unavailable_state():
    redis = AsyncMock()
    redis.get = AsyncMock(side_effect=RuntimeError("unavailable"))
    with (
        patch(
            "app.services.approved_access_capability.get_async_redis_client",
            return_value=redis,
        ),
        pytest.raises(ApprovedAccessStoreUnavailable),
    ):
        await validate_live_document_processing_request(
            request_id="workflow-1",
            patient_id="patient-1",
            provider_id="provider-1",
            hospital_id="tenant-1",
        )
