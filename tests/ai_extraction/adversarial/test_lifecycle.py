"""Adversarial lifecycle regressions for the clinical extraction commit."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.v2.pipeline_routes import CommitJobRequest, commit_extraction_job
from app.models.patient_records import TimelineEvent, Vitals
from app.models.pipeline import (
    ExtractedFieldRecord,
    ExtractionJob,
    PipelineCommit,
)
from app.security.audit_context import AuditContext, AuditDomain
from app.security.erasure_registry import _PatientErasedSignal


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Result:
    def __init__(self, *, one=None, rows=None):
        self._one = one
        self._rows = rows or []

    def scalar_one_or_none(self):
        return self._one

    def scalars(self):
        return _Scalars(self._rows)


class _TransactionalSession:
    """Small transaction spy around the real route and ingestion code paths."""

    def __init__(self, job, approved_field):
        self.job = job
        self.approved_field = approved_field
        self.pending = []
        self.persisted = []
        self.pending_outbox = []
        self.persisted_outbox = []
        self.fail_final_outbox = True
        self.rollbacks = 0
        self.commits = 0
        self.extracted_field_selects = 0

    async def execute(self, statement, _params=None):
        sql = str(statement)
        if "INSERT INTO public.audit_outbox" in sql:
            if self.fail_final_outbox and len(self.pending_outbox) == 1:
                raise RuntimeError("forced audit outbox insertion failure")
            self.pending_outbox.append(dict(_params or {}))
            return _Result()
        if "FROM extraction_jobs" in sql:
            return _Result(one=self.job)
        if "FROM extracted_fields" in sql:
            self.extracted_field_selects += 1
            if self.extracted_field_selects % 2 == 1:
                return _Result(rows=[])
            return _Result(rows=[self.approved_field])
        if "FROM pipeline_commits" in sql or "FROM timeline_events" in sql:
            return _Result(one=None)
        raise AssertionError(f"Unexpected SQL in Scenario 17: {sql}")

    def add(self, value):
        self.pending.append(value)

    async def flush(self):
        return None

    async def commit(self):
        self.persisted.extend(self.pending)
        self.persisted_outbox.extend(self.pending_outbox)
        self.pending.clear()
        self.pending_outbox.clear()
        self.commits += 1

    async def rollback(self):
        self.pending.clear()
        self.pending_outbox.clear()
        self.job.status = "review_pending"
        self.rollbacks += 1


@pytest.mark.asyncio
async def test_scenario_17_outbox_failure_rolls_back_clinical_commit_and_retry_is_safe():
    """Scenario 17: required audit durability fails the clinical commit closed."""
    patient_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    job_id = uuid.uuid4()
    document_id = uuid.uuid4()
    field_id = uuid.uuid4()
    job = ExtractionJob(
        id=job_id,
        patient_id=patient_id,
        tenant_id=tenant_id,
        uploader_id="provider-17",
        authorization_provider_id="provider-17",
        consent_request_id="workflow-scenario-17",
        document_id=document_id,
        document_type="application/pdf",
        status="review_pending",
        request_id="request-scenario-17",
        created_at=datetime.now(timezone.utc),
    )
    approved_field = ExtractedFieldRecord(
        id=field_id,
        job_id=job_id,
        patient_id=patient_id,
        field_name="blood_pressure",
        raw_value="120 over 80",
        units="mmHg",
        confidence=0.91,
        risk_level="MEDIUM_RISK",
        status="approved",
        source_document_id=document_id,
    )
    db = _TransactionalSession(job, approved_field)
    provider = SimpleNamespace(
        actor_uid="provider-17",
        hospital=SimpleNamespace(hospital_id=tenant_id),
    )
    audit_context = AuditContext.for_tenant(
        tenant_id=str(tenant_id), domain=AuditDomain.PIPELINE
    )
    capability = SimpleNamespace(request_id="workflow-scenario-17")

    patches = (
        patch(
            "app.api.v2.pipeline_routes.authorize_document_processing",
            AsyncMock(return_value=capability),
        ),
        patch("app.api.v2.pipeline_routes.assert_job_authorization_binding"),
        patch(
            "app.api.v2.pipeline_routes.current_audit_context",
            return_value=audit_context,
        ),
        patch(
            "app.api.v2.pipeline_routes.enforce_current_clinical_capability",
            AsyncMock(return_value=provider),
        ),
        patch(
            "app.api.v2.pipeline_routes.check_erasure_registry",
            AsyncMock(return_value=None),
        ),
    )
    request = Request({"type": "http", "method": "POST", "path": "/"})
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        with pytest.raises(HTTPException) as exc:
            await commit_extraction_job(
                request,
                str(job_id),
                CommitJobRequest(patient_id=str(patient_id)),
                provider,
                "consent-capability",
                db,
            )

        assert exc.value.status_code == 503
        assert exc.value.detail == {"error_code": "CLINICAL_COMMIT_AUDIT_UNAVAILABLE"}
        assert db.rollbacks == 1
        assert db.commits == 0
        assert db.persisted == []
        assert db.persisted_outbox == []
        assert db.pending_outbox == []
        assert job.status != "committed"

        db.fail_final_outbox = False
        response = await commit_extraction_job(
            request,
            str(job_id),
            CommitJobRequest(patient_id=str(patient_id)),
            provider,
            "consent-capability",
            db,
        )

    assert response["status"] == "committed"
    assert db.commits == 1
    assert sum(isinstance(row, Vitals) for row in db.persisted) == 1
    assert sum(isinstance(row, PipelineCommit) for row in db.persisted) == 1
    assert sum(isinstance(row, TimelineEvent) for row in db.persisted) == 2
    assert len(db.persisted_outbox) == 2


@pytest.mark.asyncio
async def test_commit_revocation_between_admission_and_final_checkpoint_writes_nothing():
    """A formerly valid document grant cannot authorize the later mutation."""
    patient_id, tenant_id, job_id, document_id, field_id = (
        uuid.uuid4() for _ in range(5)
    )
    job = ExtractionJob(
        id=job_id,
        patient_id=patient_id,
        tenant_id=tenant_id,
        uploader_id="provider-race",
        authorization_provider_id="provider-race",
        consent_request_id="workflow-race",
        document_id=document_id,
        document_type="application/pdf",
        status="review_pending",
        request_id="request-race",
        created_at=datetime.now(timezone.utc),
    )
    approved_field = ExtractedFieldRecord(
        id=field_id,
        job_id=job_id,
        patient_id=patient_id,
        field_name="blood_pressure",
        raw_value="120 over 80",
        units="mmHg",
        confidence=0.91,
        risk_level="MEDIUM_RISK",
        status="approved",
        source_document_id=document_id,
    )
    db = _TransactionalSession(job, approved_field)
    provider = SimpleNamespace(
        actor_uid="provider-race",
        hospital=SimpleNamespace(hospital_id=tenant_id),
    )
    capability = SimpleNamespace(request_id="workflow-race")
    revoked = HTTPException(
        status_code=403, detail={"error_code": "DOCUMENT_PROCESSING_ACCESS_REQUIRED"}
    )
    authorization = AsyncMock(side_effect=[capability, capability, revoked])
    with (
        patch(
            "app.api.v2.pipeline_routes.authorize_document_processing", authorization
        ),
        patch("app.api.v2.pipeline_routes.assert_job_authorization_binding"),
        patch(
            "app.api.v2.pipeline_routes.current_audit_context",
            return_value=AuditContext.for_tenant(
                tenant_id=str(tenant_id), domain=AuditDomain.PIPELINE
            ),
        ),
        patch(
            "app.api.v2.pipeline_routes.enforce_current_clinical_capability",
            AsyncMock(return_value=provider),
        ),
        patch(
            "app.api.v2.pipeline_routes.check_erasure_registry",
            AsyncMock(return_value=None),
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await commit_extraction_job(
                Request({"type": "http", "method": "POST", "path": "/"}),
                str(job_id),
                CommitJobRequest(patient_id=str(patient_id)),
                provider,
                "consent-capability",
                db,
            )

    assert exc_info.value.status_code == 403
    assert authorization.await_count == 3
    assert db.extracted_field_selects == 2
    assert db.persisted == []
    assert db.persisted_outbox == []
    assert db.rollbacks == 1


@pytest.mark.asyncio
async def test_commit_erasure_between_admission_and_final_checkpoint_writes_nothing():
    """An erasure state observed at the final checkpoint blocks all writes."""
    patient_id, tenant_id, job_id, document_id, field_id = (
        uuid.uuid4() for _ in range(5)
    )
    job = ExtractionJob(
        id=job_id,
        patient_id=patient_id,
        tenant_id=tenant_id,
        uploader_id="provider-erasure-race",
        authorization_provider_id="provider-erasure-race",
        consent_request_id="workflow-erasure-race",
        document_id=document_id,
        document_type="application/pdf",
        status="review_pending",
        request_id="request-erasure-race",
        created_at=datetime.now(timezone.utc),
    )
    approved_field = ExtractedFieldRecord(
        id=field_id,
        job_id=job_id,
        patient_id=patient_id,
        field_name="blood_pressure",
        raw_value="120 over 80",
        units="mmHg",
        confidence=0.91,
        risk_level="MEDIUM_RISK",
        status="approved",
        source_document_id=document_id,
    )
    db = _TransactionalSession(job, approved_field)
    provider = SimpleNamespace(
        actor_uid="provider-erasure-race",
        hospital=SimpleNamespace(hospital_id=tenant_id),
    )
    authorization = AsyncMock(
        return_value=SimpleNamespace(request_id="workflow-erasure-race")
    )
    with (
        patch(
            "app.api.v2.pipeline_routes.authorize_document_processing", authorization
        ),
        patch("app.api.v2.pipeline_routes.assert_job_authorization_binding"),
        patch(
            "app.api.v2.pipeline_routes.current_audit_context",
            return_value=AuditContext.for_tenant(
                tenant_id=str(tenant_id), domain=AuditDomain.PIPELINE
            ),
        ),
        patch(
            "app.api.v2.pipeline_routes.enforce_current_clinical_capability",
            AsyncMock(return_value=provider),
        ),
        patch(
            "app.api.v2.pipeline_routes.check_erasure_registry",
            AsyncMock(side_effect=[None, _PatientErasedSignal(str(patient_id))]),
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await commit_extraction_job(
                Request({"type": "http", "method": "POST", "path": "/"}),
                str(job_id),
                CommitJobRequest(patient_id=str(patient_id)),
                provider,
                "consent-capability",
                db,
            )

    assert exc_info.value.status_code == 410
    assert exc_info.value.detail == {"error_code": "PATIENT_ACCESS_ERASED"}
    assert authorization.await_count == 3
    assert db.extracted_field_selects == 2
    assert db.persisted == []
    assert db.persisted_outbox == []
    assert db.rollbacks == 1


@pytest.mark.asyncio
async def test_commit_provider_revocation_after_ingestion_preflight_writes_nothing():
    """Provider trust is current again after the last idempotency read."""
    patient_id, tenant_id, job_id, document_id, field_id = (
        uuid.uuid4() for _ in range(5)
    )
    job = ExtractionJob(
        id=job_id,
        patient_id=patient_id,
        tenant_id=tenant_id,
        uploader_id="provider-mutation-race",
        authorization_provider_id="provider-mutation-race",
        consent_request_id="workflow-mutation-race",
        document_id=document_id,
        document_type="application/pdf",
        status="review_pending",
        request_id="request-mutation-race",
        created_at=datetime.now(timezone.utc),
    )
    approved_field = ExtractedFieldRecord(
        id=field_id,
        job_id=job_id,
        patient_id=patient_id,
        field_name="blood_pressure",
        raw_value="120 over 80",
        units="mmHg",
        confidence=0.91,
        risk_level="MEDIUM_RISK",
        status="approved",
        source_document_id=document_id,
    )
    db = _TransactionalSession(job, approved_field)
    provider = SimpleNamespace(
        actor_uid="provider-mutation-race",
        hospital_id=tenant_id,
        hospital=SimpleNamespace(hospital_id=tenant_id),
    )
    denied = HTTPException(
        status_code=403, detail={"error_code": "CLINICAL_ELIGIBILITY_DENIED"}
    )
    provider_gate = AsyncMock(side_effect=[provider, denied])
    with (
        patch(
            "app.api.v2.pipeline_routes.authorize_document_processing",
            AsyncMock(
                return_value=SimpleNamespace(
                    patient_id=str(patient_id),
                    request_id="workflow-mutation-race",
                )
            ),
        ),
        patch("app.api.v2.pipeline_routes.assert_job_authorization_binding"),
        patch(
            "app.api.v2.pipeline_routes.current_audit_context",
            return_value=AuditContext.for_tenant(
                tenant_id=str(tenant_id), domain=AuditDomain.PIPELINE
            ),
        ),
        patch(
            "app.api.v2.pipeline_routes.enforce_current_clinical_capability",
            provider_gate,
        ),
        patch(
            "app.api.v2.pipeline_routes.check_erasure_registry",
            AsyncMock(return_value=None),
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await commit_extraction_job(
                Request({"type": "http", "method": "POST", "path": "/"}),
                str(job_id),
                CommitJobRequest(patient_id=str(patient_id)),
                provider,
                "consent-capability",
                db,
            )

    assert exc_info.value.status_code == 403
    assert provider_gate.await_count == 2
    assert db.extracted_field_selects == 2
    assert db.persisted == []
    assert db.persisted_outbox == []
    assert db.rollbacks == 1


@pytest.mark.asyncio
async def test_commit_rejects_fresh_provider_context_that_breaks_job_binding():
    """The patient check consumes the callback's fresh canonical context."""
    patient_id, tenant_id, job_id, document_id, field_id = (
        uuid.uuid4() for _ in range(5)
    )
    job = ExtractionJob(
        id=job_id,
        patient_id=patient_id,
        tenant_id=tenant_id,
        uploader_id="provider-binding-race",
        authorization_provider_id="provider-binding-race",
        consent_request_id="workflow-binding-race",
        document_id=document_id,
        document_type="application/pdf",
        status="review_pending",
        request_id="request-binding-race",
        created_at=datetime.now(timezone.utc),
    )
    approved_field = ExtractedFieldRecord(
        id=field_id,
        job_id=job_id,
        patient_id=patient_id,
        field_name="blood_pressure",
        raw_value="120 over 80",
        units="mmHg",
        confidence=0.91,
        risk_level="MEDIUM_RISK",
        status="approved",
        source_document_id=document_id,
    )
    db = _TransactionalSession(job, approved_field)
    provider = SimpleNamespace(
        actor_uid="provider-binding-race",
        hospital_id=tenant_id,
        hospital=SimpleNamespace(hospital_id=tenant_id),
    )
    different_provider = SimpleNamespace(
        actor_uid="different-provider",
        hospital_id=tenant_id,
        hospital=SimpleNamespace(hospital_id=tenant_id),
    )
    capability = SimpleNamespace(
        patient_id=str(patient_id), request_id="workflow-binding-race"
    )
    with (
        patch(
            "app.api.v2.pipeline_routes.authorize_document_processing",
            AsyncMock(return_value=capability),
        ),
        patch(
            "app.api.v2.pipeline_routes.current_audit_context",
            return_value=AuditContext.for_tenant(
                tenant_id=str(tenant_id), domain=AuditDomain.PIPELINE
            ),
        ),
        patch(
            "app.api.v2.pipeline_routes.enforce_current_clinical_capability",
            AsyncMock(side_effect=[provider, different_provider]),
        ),
        patch(
            "app.api.v2.pipeline_routes.check_erasure_registry",
            AsyncMock(return_value=None),
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await commit_extraction_job(
                Request({"type": "http", "method": "POST", "path": "/"}),
                str(job_id),
                CommitJobRequest(patient_id=str(patient_id)),
                provider,
                "consent-capability",
                db,
            )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == {"error_code": "CROSS_PROVIDER_JOB_ACCESS"}
    assert db.extracted_field_selects == 2
    assert db.persisted == []
    assert db.persisted_outbox == []
    assert db.rollbacks == 1
