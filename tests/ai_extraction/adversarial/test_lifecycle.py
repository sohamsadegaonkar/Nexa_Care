"""Adversarial lifecycle regressions for the clinical extraction commit."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api.v2.pipeline_routes import CommitJobRequest, commit_extraction_job
from app.models.patient_records import TimelineEvent, Vitals
from app.models.pipeline import (
    ExtractedFieldRecord,
    ExtractionJob,
    PipelineCommit,
)
from app.security.audit_context import AuditContext, AuditDomain


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
    )
    with patches[0], patches[1], patches[2]:
        with pytest.raises(HTTPException) as exc:
            await commit_extraction_job(
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
