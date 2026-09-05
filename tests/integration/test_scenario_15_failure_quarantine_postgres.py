"""Permanent real-PostgreSQL evidence for the Scenario 15 lifecycle."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.ai.extractor import (
    DEMO_MEDICAL_DOCUMENT_CONTRACT_VERSION,
    DemoExtractionProvider,
    ProviderAttemptTrace,
    ProviderAttemptOutcome,
    ProviderTimeoutError,
)
from app.core.config import get_document_extraction_config
from app.core.database import get_async_engine, get_session_factory
from app.models.patient_records import LabResult, PatientRecord, TimelineEvent, Vitals
from app.models.pipeline import (
    AdjudicationCaseRecord,
    DocumentStorage,
    ExtractionAttemptEventRecord,
    ExtractionCandidateRecord,
    ExtractionDecisionRecord,
    ExtractionFailureQuarantineRecord,
    ExtractionJob,
    ExtractionRoutingRecord,
)
from app.models.provider import AffiliationType
from app.models.provider_context import (
    AffiliationContext,
    HospitalContext,
    ProviderContext,
    ProviderIdentityContext,
)
from app.security.document_processing_policy import (
    DOCUMENT_PROCESSING_PURPOSE,
    DOCUMENT_PROCESSING_SCOPE,
)
from app.services.approved_access_capability import (
    invalidate_request,
    issue_from_approved_request,
)
from app.services.failure_quarantine import (
    apply_failure_quarantine_disposition,
    escalate_expired_failure_quarantines,
)
from app.services.pipeline_orchestrator import process_extraction_job
from tests.ai_extraction.adversarial.scenario_catalog import (
    RUNTIME_AUTO_COMMIT_APPROVED,
    RUNTIME_AUTO_COMMIT_ENABLED,
)
from tests.helpers.qualification_infra import seed_qualification_provider_trust


pytestmark = [pytest.mark.integration, pytest.mark.postgres, pytest.mark.redis]


def _required_env() -> tuple[str, str, str]:
    database_url = os.getenv("TEST_DATABASE_URL")
    redis_url = os.getenv("TEST_REDIS_URL")
    redis_prefix = os.getenv("TEST_REDIS_PREFIX")
    if not database_url or not redis_url:
        pytest.skip("TEST_DATABASE_URL and TEST_REDIS_URL are required")
    if os.getenv("NEXA_ALLOW_DISPOSABLE_TEST_DB") != "1":
        pytest.skip("NEXA_ALLOW_DISPOSABLE_TEST_DB=1 is required")
    if urlsplit(database_url).hostname not in {"127.0.0.1", "localhost"}:
        pytest.fail("TEST_DATABASE_URL must be loopback-only")
    if urlsplit(redis_url).hostname not in {"127.0.0.1", "localhost"}:
        pytest.fail("TEST_REDIS_URL must be loopback-only")
    if not urlsplit(database_url).path.lstrip("/").startswith("nexa_qual_"):
        pytest.fail("TEST_DATABASE_URL must name a nexa_qual_ disposable database")
    if not redis_prefix or not redis_prefix.startswith("nexa-qual-"):
        pytest.fail("TEST_REDIS_PREFIX must be a dedicated nexa-qual- prefix")
    return database_url, redis_url, redis_prefix


@pytest_asyncio.fixture
async def local_scenario_15_services(monkeypatch):
    database_url, redis_url, redis_prefix = _required_env()
    engine = create_async_engine(database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    redis = Redis.from_url(redis_url, decode_responses=True)
    previous_database = os.environ.get("DATABASE_URL")
    previous_redis = os.environ.get("UPSTASH_REDIS_URL")
    os.environ["DATABASE_URL"] = database_url
    os.environ["UPSTASH_REDIS_URL"] = redis_url
    get_session_factory.cache_clear()
    get_async_engine.cache_clear()
    monkeypatch.setattr(
        "app.services.approved_access_capability.get_async_redis_client",
        lambda: redis,
    )
    await redis.ping()
    try:
        yield factory, redis, redis_prefix
    finally:
        close = getattr(redis, "aclose", None) or getattr(redis, "close")
        result = close()
        if hasattr(result, "__await__"):
            await result
        pool_close = getattr(redis.connection_pool, "aclose", None)
        if pool_close is not None:
            await pool_close()
        else:
            await redis.connection_pool.disconnect(inuse_connections=True)
        await engine.dispose()
        if previous_database is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database
        if previous_redis is None:
            os.environ.pop("UPSTASH_REDIS_URL", None)
        else:
            os.environ["UPSTASH_REDIS_URL"] = previous_redis
        get_session_factory.cache_clear()
        get_async_engine.cache_clear()


class _AlwaysRetryableFailureProvider(DemoExtractionProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def extract_bytes(
        self, document_bytes: bytes, *, mime_type: str, request_id: str
    ):
        assert document_bytes == b"synthetic-scenario-15-document"
        assert mime_type == "application/pdf"
        assert request_id
        self.calls += 1
        trace = ProviderAttemptTrace(
            provider_subattempt_number=1,
            provider_adapter="demo",
            provider_contract_version=DEMO_MEDICAL_DOCUMENT_CONTRACT_VERSION,
            provider_model_version="synthetic-scenario-15",
            outcome=ProviderAttemptOutcome.TIMEOUT,
            error_code="EXTRACTION_PROVIDER_TIMEOUT",
            response_complete=False,
            occurred_at=datetime.now(timezone.utc),
        )
        raise ProviderTimeoutError(
            "synthetic provider failure body must never be persisted",
            provider_attempt_traces=(trace,),
        )


class _SyntheticDocumentStorage:
    async def get_document_bytes(
        self, storage_ref: str, *, tenant_id: str, patient_id: str
    ) -> bytes:
        assert storage_ref.startswith("scenario-15-")
        assert tenant_id
        assert patient_id
        return b"synthetic-scenario-15-document"


def _reviewer(tenant_id: uuid.UUID) -> ProviderContext:
    provider_id = uuid.uuid4()
    return ProviderContext(
        provider=ProviderIdentityContext(
            provider_id=provider_id,
            display_name="Synthetic Scenario 15 reviewer",
            contact_email="scenario15-reviewer@example.test",
        ),
        hospital=HospitalContext(
            hospital_id=tenant_id,
            facility_code="SCENARIO-15",
            display_name="Synthetic Scenario 15 facility",
        ),
        affiliation=AffiliationContext(
            affiliation_id=uuid.uuid4(),
            affiliation_type=AffiliationType.PERMANENT,
            roles=["clinician", "clinical_reviewer"],
            is_primary=True,
        ),
    )


async def _counts(
    db, *, patient_id: uuid.UUID, job_id: uuid.UUID
) -> dict[str, int | None]:
    values = {}
    for name, model, column in (
        ("candidates", ExtractionCandidateRecord, ExtractionCandidateRecord.job_id),
        ("decisions", ExtractionDecisionRecord, ExtractionDecisionRecord.job_id),
        ("routing", ExtractionRoutingRecord, ExtractionRoutingRecord.job_id),
        ("attempts", ExtractionAttemptEventRecord, ExtractionAttemptEventRecord.job_id),
    ):
        values[name] = await db.scalar(
            select(func.count()).select_from(model).where(column == job_id)
        )
    for name, model in (
        ("patient_records", PatientRecord),
        ("vitals", Vitals),
        ("labs", LabResult),
        ("timeline", TimelineEvent),
    ):
        values[name] = await db.scalar(
            select(func.count())
            .select_from(model)
            .where(model.patient_id == patient_id)
        )
    case = await db.scalar(
        select(AdjudicationCaseRecord.clinical_committed_at).where(
            AdjudicationCaseRecord.job_id == job_id
        )
    )
    values["clinical_committed_at"] = case
    return values


async def _attempt_snapshot(db, job_id: uuid.UUID) -> list[tuple[object, ...]]:
    rows = (
        await db.execute(
            select(ExtractionAttemptEventRecord)
            .where(ExtractionAttemptEventRecord.job_id == job_id)
            .order_by(
                ExtractionAttemptEventRecord.job_attempt_number,
                ExtractionAttemptEventRecord.provider_subattempt_number,
            )
        )
    ).scalars()
    return [
        (
            row.id,
            row.tenant_id,
            row.patient_id,
            row.source_document_id,
            row.job_attempt_number,
            row.provider_subattempt_number,
            row.provider_adapter,
            row.provider_contract_version,
            row.provider_model_version,
            row.outcome,
            row.error_code,
            row.response_complete,
            row.occurred_at,
        )
        for row in rows
    ]


@pytest.mark.asyncio
async def test_scenario_15_retry_exhaustion_escalates_to_manual_disposition_without_clinical_commit(
    local_scenario_15_services, monkeypatch
):
    factory, redis, redis_prefix = local_scenario_15_services
    tenant_id, patient_id, document_id, job_id = (uuid.uuid4() for _ in range(4))
    request_id = str(uuid.uuid4())
    reviewer = _reviewer(tenant_id)
    now = datetime.now(timezone.utc)
    provider = _AlwaysRetryableFailureProvider()
    monkeypatch.setattr(
        "app.services.pipeline_orchestrator.get_medical_document_extractor",
        lambda _config=None: provider,
    )
    monkeypatch.setattr(
        "app.services.pipeline_orchestrator.get_document_storage",
        lambda: _SyntheticDocumentStorage(),
    )

    request_data = {
        "request_id": request_id,
        "provider_id": reviewer.actor_uid,
        "hospital_id": str(tenant_id),
        "patient_id": str(patient_id),
        "status": "approved",
        "access_expires_at": (now + timedelta(minutes=20)).isoformat(),
        "purpose": DOCUMENT_PROCESSING_PURPOSE,
        "scope": DOCUMENT_PROCESSING_SCOPE,
    }
    await redis.set(f"consent_request:{request_id}", json.dumps(request_data), ex=1200)
    await issue_from_approved_request(request_data=request_data)

    try:
        async with factory() as db:
            await seed_qualification_provider_trust(
                db,
                provider_id=reviewer.provider.provider_id,
                hospital_id=tenant_id,
                facility_code=f"S15-{uuid.uuid4().hex[:10]}",
                roles=["clinician", "clinical_reviewer"],
                now=now,
            )
            await db.execute(
                text("INSERT INTO public.patients (patient_uuid) VALUES (:patient)"),
                {"patient": patient_id},
            )
            db.add(
                DocumentStorage(
                    id=document_id,
                    patient_id=patient_id,
                    tenant_id=tenant_id,
                    uploader_id="synthetic-uploader",
                    storage_ref=f"scenario-15-{document_id}",
                    content_type="application/pdf",
                    size=32,
                    content_hash=uuid.uuid4().hex * 2,
                    original_filename=None,
                    upload_purpose="qualification",
                    consent_session_id=request_id,
                    source_system="SYNTHETIC_SCENARIO_15",
                    uploaded_at=now,
                )
            )
            db.add(
                ExtractionJob(
                    id=job_id,
                    patient_id=patient_id,
                    tenant_id=tenant_id,
                    uploader_id="synthetic-uploader",
                    authorization_provider_id=reviewer.actor_uid,
                    consent_request_id=request_id,
                    document_id=document_id,
                    document_type="application/pdf",
                    status="queued",
                    request_id=f"scenario-15-job-{uuid.uuid4().hex}",
                    attempt_count=0,
                    retryable=False,
                    version=1,
                    created_at=now,
                    authorization_initiated_at=now,
                    authorization_authentication_method="PROVIDER_SESSION",
                    authorization_mfa_verified_at=now,
                    authorization_assurance_policy_version="clinical-contact-email-and-phone/v1",
                )
            )
            await db.commit()

        config = get_document_extraction_config()
        job_budget = config.job_max_attempts
        assert 1 <= job_budget <= 5
        baseline = None
        for attempt_number in range(1, job_budget + 1):
            async with factory() as db:
                result = await process_extraction_job(str(job_id), db)
                job = await db.scalar(
                    select(ExtractionJob).where(ExtractionJob.id == job_id)
                )
                assert job is not None
                counts = await _counts(db, patient_id=patient_id, job_id=job_id)
                if attempt_number < job_budget:
                    assert result["status"] == "extraction_failed_retryable"
                    assert result["retryable"] is True
                    assert job.status == "extraction_failed_retryable"
                    assert job.retryable is True
                    assert counts["attempts"] == attempt_number
                    assert counts["candidates"] == 0
                    assert counts["decisions"] == 0
                    assert counts["routing"] == 0
                    baseline = counts
                    assert not await db.scalar(
                        select(func.count())
                        .select_from(ExtractionFailureQuarantineRecord)
                        .where(ExtractionFailureQuarantineRecord.job_id == job_id)
                    )
                else:
                    assert result["status"] == "quarantined"
                    assert result["retryable"] is False
                    assert job.status == "quarantined"
                    assert job.retryable is False
                    assert counts["attempts"] == job_budget
                    assert counts["candidates"] == 0
                    assert counts["decisions"] == 0
                    assert counts["routing"] == 0
                    assert baseline is not None

        assert provider.calls == job_budget

        async with factory() as db:
            job = await db.scalar(
                select(ExtractionJob).where(ExtractionJob.id == job_id)
            )
            assert job is not None
            binding = (job.id, job.tenant_id, job.patient_id, job.document_id)
            cases = (
                (
                    await db.execute(
                        select(ExtractionFailureQuarantineRecord).where(
                            ExtractionFailureQuarantineRecord.job_id == job_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(cases) == 1
            case = cases[0]
            assert (
                case.job_id,
                case.tenant_id,
                case.patient_id,
                case.source_document_id,
            ) == binding
            assert case.reason_code == "PROVIDER_RETRY_EXHAUSTED"
            assert case.status == "PENDING"
            assert case.version == 1
            assert case.review_deadline is not None
            assert case.disposition is None
            assert case.disposed_at is None
            assert case.disposed_by_provider_id is None
            attempts_before = await _attempt_snapshot(db, job_id)
            assert len(attempts_before) == job_budget
            assert [row[4] for row in attempts_before] == list(range(1, job_budget + 1))
            assert all(
                row[9] == "TIMEOUT" and row[10] == "EXTRACTION_PROVIDER_TIMEOUT"
                for row in attempts_before
            )
            assert all(row[11] is False for row in attempts_before)
            assert all(
                "synthetic provider failure body" not in repr(row)
                for row in attempts_before
            )
            counts_after_exhaustion = await _counts(
                db, patient_id=patient_id, job_id=job_id
            )
            assert counts_after_exhaustion["patient_records"] == 0
            assert counts_after_exhaustion["vitals"] == 0
            assert counts_after_exhaustion["labs"] == 0
            assert counts_after_exhaustion["timeline"] == 0
            assert counts_after_exhaustion["clinical_committed_at"] is None

            events = (
                (
                    await db.execute(
                        text(
                            "SELECT event_type FROM public.audit_outbox "
                            "WHERE tenant_id = :tenant AND patient_id = :patient "
                            "AND event_type IN ('EXTRACTION_FAILURE_QUARANTINED', "
                            "'EXTRACTION_FAILURE_QUARANTINE_ESCALATED', "
                            "'EXTRACTION_FAILURE_QUARANTINE_DISPOSITION_APPLIED')"
                        ),
                        {"tenant": str(tenant_id), "patient": str(patient_id)},
                    )
                )
                .scalars()
                .all()
            )
            assert events.count("EXTRACTION_FAILURE_QUARANTINED") == 1

        async with factory() as db:
            assert (
                await escalate_expired_failure_quarantines(
                    db, batch_size=100, now=case.review_deadline
                )
                >= 1
            )
            await db.commit()

        async with factory() as db:
            case = await db.scalar(
                select(ExtractionFailureQuarantineRecord).where(
                    ExtractionFailureQuarantineRecord.id == case.id
                )
            )
            assert case is not None
            assert case.status == "ESCALATED"
            assert case.escalated_at is not None
            assert case.version == 2
            assert case.disposition is None
            counts_after_escalation = await _counts(
                db, patient_id=patient_id, job_id=job_id
            )
            assert counts_after_escalation["candidates"] == 0
            assert counts_after_escalation["decisions"] == 0
            assert counts_after_escalation["routing"] == 0
            assert counts_after_escalation["vitals"] == 0
            assert counts_after_escalation["labs"] == 0
            assert counts_after_escalation["timeline"] == 0
            assert counts_after_escalation["clinical_committed_at"] is None

        async with factory() as db:
            case = await apply_failure_quarantine_disposition(
                db,
                case_id=case.id,
                provider=reviewer,
                disposition="RETAIN_SOURCE_NO_CLINICAL_COMMIT",
                expected_version=case.version,
                idempotency_key=f"scenario-15-disposition-{uuid.uuid4().hex}",
            )
            await db.commit()

        async with factory() as db:
            final_case = await db.scalar(
                select(ExtractionFailureQuarantineRecord).where(
                    ExtractionFailureQuarantineRecord.id == case.id
                )
            )
            final_job = await db.scalar(
                select(ExtractionJob).where(ExtractionJob.id == job_id)
            )
            assert final_case is not None and final_job is not None
            assert final_case.status == "DISPOSED"
            assert final_case.disposition == "RETAIN_SOURCE_NO_CLINICAL_COMMIT"
            assert final_case.disposed_at is not None
            assert final_case.disposed_by_provider_id == reviewer.provider.provider_id
            assert final_case.version == 3
            assert (
                final_job.id,
                final_job.tenant_id,
                final_job.patient_id,
                final_job.document_id,
            ) == binding
            assert final_job.status == "quarantined"
            assert final_job.retryable is False
            final_counts = await _counts(db, patient_id=patient_id, job_id=job_id)
            assert final_counts["candidates"] == 0
            assert final_counts["decisions"] == 0
            assert final_counts["routing"] == 0
            assert final_counts["patient_records"] == 0
            assert final_counts["vitals"] == 0
            assert final_counts["labs"] == 0
            assert final_counts["timeline"] == 0
            assert final_counts["clinical_committed_at"] is None
            assert await _attempt_snapshot(db, job_id) == attempts_before
            events = (
                (
                    await db.execute(
                        text(
                            "SELECT event_type FROM public.audit_outbox "
                            "WHERE tenant_id = :tenant AND patient_id = :patient"
                        ),
                        {"tenant": str(tenant_id), "patient": str(patient_id)},
                    )
                )
                .scalars()
                .all()
            )
            assert events.count("EXTRACTION_FAILURE_QUARANTINE_ESCALATED") == 1
            assert (
                events.count("EXTRACTION_FAILURE_QUARANTINE_DISPOSITION_APPLIED") == 1
            )

        assert RUNTIME_AUTO_COMMIT_ENABLED is False
        assert RUNTIME_AUTO_COMMIT_APPROVED is False
    finally:
        await invalidate_request(request_id)
