"""Real PostgreSQL adjudication ordering, retry, and commit contracts."""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.adjudication import AdjudicationOutcome, AdjudicationReasonCode
from app.models.patient_records import TimelineEvent, Vitals
from app.models.pipeline import (
    AdjudicationCaseRecord,
    AdjudicationSubmissionRecord,
    DocumentStorage,
    ExtractionJob,
)
from app.models.provider import AffiliationType, HospitalRegistry
from app.models.provider_context import (
    AffiliationContext,
    HospitalContext,
    ProviderContext,
    ProviderIdentityContext,
)
from app.security.document_processing_policy import DocumentProcessingOperation
from app.security.erasure_registry import (
    ErasureRegistryUnavailable,
    _PatientErasedSignal,
)
from app.services.adjudication import (
    AdjudicationError,
    _live_access,
    commit_submission,
    create_case,
    read_source_document,
    rotate_review_session,
    submit_case,
)

pytestmark = pytest.mark.postgres


def _url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


def _provider(tenant: uuid.UUID) -> ProviderContext:
    return ProviderContext(
        provider=ProviderIdentityContext(
            provider_id=uuid.uuid4(),
            display_name="Qualification reviewer",
            contact_email="qualification@example.test",
        ),
        hospital=HospitalContext(
            hospital_id=tenant,
            facility_code="M52",
            display_name="Qualification facility",
        ),
        affiliation=AffiliationContext(
            affiliation_id=uuid.uuid4(),
            affiliation_type=AffiliationType.PERMANENT,
            roles=["clinician"],
            is_primary=True,
        ),
    )


async def _case(factory):
    tenant, patient, document, job = (uuid.uuid4() for _ in range(4))
    provider = _provider(tenant)
    review_session = f"review-{uuid.uuid4().hex}"
    async with factory() as db:
        db.add(
            HospitalRegistry(
                id=tenant,
                facility_code=f"M52-{uuid.uuid4().hex[:12]}",
                legal_name="Qualification facility",
                display_name="Qualification facility",
                country_code="IN",
                is_active=True,
            )
        )
        await db.flush()
        await db.execute(
            text("INSERT INTO public.patients (patient_uuid) VALUES (:id)"),
            {"id": patient},
        )
        db.add(
            DocumentStorage(
                id=document,
                patient_id=patient,
                tenant_id=tenant,
                uploader_id=provider.actor_uid,
                storage_ref=f"qualification-{uuid.uuid4().hex}",
                content_type="application/pdf",
                size=16,
                content_hash=uuid.uuid4().hex * 2,
                original_filename=None,
                upload_purpose="qualification",
                consent_session_id=str(uuid.uuid4()),
                source_system="qualification",
                uploaded_at=datetime.now(timezone.utc),
            )
        )
        db.add(
            ExtractionJob(
                id=job,
                patient_id=patient,
                tenant_id=tenant,
                uploader_id=provider.actor_uid,
                authorization_provider_id=provider.actor_uid,
                consent_request_id=str(uuid.uuid4()),
                document_id=document,
                document_type="qualification",
                status="source_only",
                request_id=f"m52-{uuid.uuid4().hex}",
                attempt_count=1,
                retryable=False,
                version=1,
                created_at=datetime.now(timezone.utc),
            )
        )
        await db.flush()
        with patch("app.services.adjudication._live_access", AsyncMock()):
            case = await create_case(
                db,
                provider=provider,
                idempotency_key=f"case-{uuid.uuid4().hex}",
                review_session_id=review_session,
                job_id=job,
            )
        case_id = case.id
        await db.commit()
    return provider, patient, case_id, review_session


def _payload(effective_at: datetime, value: float = 72.0):
    return [
        {
            "kind": "VITAL",
            "vital_type": "HEART_RATE",
            "reviewer_entered_value": value,
            "normalized_value": value,
            "unit": "beats/min",
            "effective_at": effective_at,
            "page_number": 0,
            "provenance_type": "HUMAN_TRANSCRIBED",
        }
    ]


async def _submit(factory, provider, case_id, session_id, key, payload):
    async with factory() as db:
        try:
            with patch("app.services.adjudication._live_access", AsyncMock()):
                row = await submit_case(
                    db,
                    case_id=case_id,
                    provider=provider,
                    review_session_id=session_id,
                    outcome=AdjudicationOutcome.ACCEPTED,
                    fields=payload,
                    reason_codes=[AdjudicationReasonCode.SOURCE_VERIFIED],
                    idempotency_key=key,
                )
                await db.commit()
                return row.id
        except AdjudicationError as exc:
            await db.rollback()
            return exc.code


@pytest.mark.asyncio
async def test_jsonb_commit_retry_collision_and_concurrency():
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        provider, patient, case_id, session_id = await _case(factory)
        frozen = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
        payload = _payload(frozen)
        keys = [f"submit-{uuid.uuid4().hex}", f"submit-{uuid.uuid4().hex}"]
        with patch("app.services.adjudication._live_access", AsyncMock()):
            results = await asyncio.gather(
                *(
                    _submit(factory, provider, case_id, session_id, key, payload)
                    for key in keys
                )
            )
        accepted = [value for value in results if isinstance(value, uuid.UUID)]
        assert len(accepted) == 1
        assert results.count("ADJUDICATION_ALREADY_RESOLVED") == 1
        submission_id = accepted[0]
        accepted_key = keys[results.index(submission_id)]
        assert (
            await _submit(
                factory, provider, case_id, session_id, accepted_key, payload
            )
            == submission_id
        )
        assert (
            await _submit(
                factory,
                provider,
                case_id,
                session_id,
                accepted_key,
                _payload(frozen.replace(minute=1)),
            )
            == "ADJUDICATION_IDEMPOTENCY_COLLISION"
        )

        async def commit():
            async with factory() as db:
                with patch("app.services.adjudication._live_access", AsyncMock()):
                    case = await commit_submission(
                        db,
                        submission_id=submission_id,
                        provider=provider,
                        review_session_id=session_id,
                    )
                    await db.commit()
                    return case.id

        with patch("app.services.adjudication._live_access", AsyncMock()):
            assert await asyncio.gather(commit(), commit()) == [case_id, case_id]
        async with factory() as db:
            case = (
                await db.execute(
                    select(AdjudicationCaseRecord).where(
                        AdjudicationCaseRecord.id == case_id
                    )
                )
            ).scalar_one()
            assert case.accepted_submission_id == submission_id
            assert (
                await db.execute(
                    select(func.count(AdjudicationSubmissionRecord.id)).where(
                        AdjudicationSubmissionRecord.case_id == case_id
                    )
                )
            ).scalar_one() == 1
            assert (
                await db.execute(
                    select(func.count(Vitals.id)).where(Vitals.patient_id == patient)
                )
            ).scalar_one() == 1
            assert (
                await db.execute(
                    select(func.count(TimelineEvent.id)).where(
                        TimelineEvent.patient_id == patient
                    )
                )
            ).scalar_one() == 1
    finally:
        await engine.dispose()


async def _expect_code(awaitable, code):
    with pytest.raises(AdjudicationError) as exc:
        await awaitable
    assert exc.value.code == code


@pytest.mark.asyncio
async def test_source_recovery_consent_erasure_and_audit_privacy():
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        provider, _, case_id, old_session = await _case(factory)

        async def recover(value):
            async with factory() as db:
                case = await rotate_review_session(
                    db,
                    case_id=case_id,
                    provider=provider,
                    new_review_session_id=value,
                )
                await db.commit()
                return case.review_session_id

        sessions = [
            f"recovered-{uuid.uuid4().hex}",
            f"recovered-{uuid.uuid4().hex}",
        ]
        with patch("app.services.adjudication._live_access", AsyncMock()):
            assert set(await asyncio.gather(*(recover(value) for value in sessions))) == set(
                sessions
            )
        async with factory() as db:
            case = (
                await db.execute(
                    select(AdjudicationCaseRecord).where(
                        AdjudicationCaseRecord.id == case_id
                    )
                )
            ).scalar_one()
            assert case.version == 3
            current = case.review_session_id
            storage = SimpleNamespace(
                get_document_bytes=AsyncMock(return_value=b"qualification-source")
            )
            with (
                patch("app.services.adjudication._live_access", AsyncMock()),
                patch(
                    "app.services.adjudication.get_document_storage",
                    return_value=storage,
                ),
            ):
                await _expect_code(
                    read_source_document(
                        db,
                        case_id=case_id,
                        provider=provider,
                        review_session_id=old_session,
                    ),
                    "ADJUDICATION_SESSION_MISMATCH",
                )
                content, kind = await read_source_document(
                    db,
                    case_id=case_id,
                    provider=provider,
                    review_session_id=current,
                )
                assert content == b"qualification-source"
                assert kind == "application/pdf"
            job = (
                await db.execute(
                    select(ExtractionJob).where(ExtractionJob.id == case.job_id)
                )
            ).scalar_one()
            with patch(
                "app.services.adjudication.validate_live_document_processing_request",
                AsyncMock(return_value=None),
            ):
                await _expect_code(
                    _live_access(
                        db,
                        job=job,
                        provider=provider,
                        operation=DocumentProcessingOperation.READ_DOCUMENT_SOURCE,
                    ),
                    "ADJUDICATION_CONSENT_INACTIVE",
                )
            capability = SimpleNamespace(
                allowed_operations=[
                    DocumentProcessingOperation.READ_DOCUMENT_SOURCE.value
                ]
            )
            for failure, code in (
                (
                    _PatientErasedSignal("opaque-reference"),
                    "ADJUDICATION_ERASURE_ACCESS_BLOCKED",
                ),
                (
                    ErasureRegistryUnavailable("internal-detail"),
                    "ADJUDICATION_ERASURE_REGISTRY_UNAVAILABLE",
                ),
            ):
                with (
                    patch(
                        "app.services.adjudication.validate_live_document_processing_request",
                        AsyncMock(return_value=capability),
                    ),
                    patch(
                        "app.services.adjudication.check_erasure_registry",
                        AsyncMock(side_effect=failure),
                    ),
                ):
                    await _expect_code(
                        _live_access(
                            db,
                            job=job,
                            provider=provider,
                            operation=DocumentProcessingOperation.READ_DOCUMENT_SOURCE,
                        ),
                        code,
                    )
            await db.commit()
            payloads = "\n".join(
                str(value)
                for value in (
                    await db.execute(
                        text(
                            "SELECT payload FROM public.audit_outbox "
                            "WHERE tenant_id = :tenant"
                        ),
                        {"tenant": str(case.tenant_id)},
                    )
                ).scalars()
            )
            for protected in (
                old_session,
                current,
                "qualification-source",
                "opaque-reference",
                "internal-detail",
            ):
                assert protected not in payloads
    finally:
        await engine.dispose()
