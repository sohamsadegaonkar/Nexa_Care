"""Disposable PostgreSQL proof for registration graph and audit atomicity."""

from __future__ import annotations

import asyncio
import os
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.patient import Patient
from app.models.patient_auth_identity import PatientAuthIdentity
from app.models.patient_records import PatientRecord
from app.services.patient_registration_service import (
    finalize_patient_registration,
    registration_audit_idempotency_key,
)

pytestmark = pytest.mark.postgres


def _url() -> str:
    value = os.getenv("TEST_DATABASE_URL")
    if not value:
        pytest.skip("TEST_DATABASE_URL is not configured")
    normalized = value.replace("postgresql://", "postgresql+asyncpg://", 1)
    if "127.0.0.1" not in normalized and "localhost" not in normalized:
        pytest.fail("TEST_DATABASE_URL must be loopback-only")
    if "nexa_qual_" not in normalized:
        pytest.fail("TEST_DATABASE_URL must name a disposable nexa_qual_ database")
    return normalized


async def _cleanup(factory, subject: str, attempt_id: str) -> None:
    async with factory() as db:
        patient_ids = list(
            (
                await db.scalars(
                    select(PatientAuthIdentity.patient_id).where(
                        PatientAuthIdentity.provider == "supabase",
                        PatientAuthIdentity.provider_subject == subject,
                    )
                )
            ).all()
        )
        await db.execute(
            text("DELETE FROM public.audit_outbox WHERE idempotency_key = :key"),
            {"key": registration_audit_idempotency_key(attempt_id)},
        )
        if patient_ids:
            await db.execute(
                delete(PatientRecord).where(PatientRecord.patient_id.in_(patient_ids))
            )
            await db.execute(
                delete(PatientAuthIdentity).where(
                    PatientAuthIdentity.patient_id.in_(patient_ids)
                )
            )
            await db.execute(
                delete(Patient).where(Patient.patient_uuid.in_(patient_ids))
            )
        await db.commit()


@pytest.mark.asyncio
async def test_registration_concurrency_creates_one_complete_audited_graph() -> None:
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    subject = f"registration-concurrent-{uuid.uuid4().hex}"
    attempt_id = uuid.uuid4().hex
    try:

        async def finalize_once():
            async with factory() as db:
                return await finalize_patient_registration(
                    db, provider_subject=subject, attempt_id=attempt_id
                )

        first, second = await asyncio.gather(finalize_once(), finalize_once())
        assert first.patient_id == second.patient_id
        assert {first.created, second.created} == {False, True}

        async with factory() as db:
            identities = list(
                (
                    await db.scalars(
                        select(PatientAuthIdentity).where(
                            PatientAuthIdentity.provider == "supabase",
                            PatientAuthIdentity.provider_subject == subject,
                        )
                    )
                ).all()
            )
            assert len(identities) == 1
            patient_id = identities[0].patient_id
            assert (
                int(
                    await db.scalar(
                        select(func.count())
                        .select_from(PatientRecord)
                        .where(PatientRecord.patient_id == patient_id)
                    )
                    or 0
                )
                == 1
            )
            outbox = await db.execute(
                text(
                    "SELECT event_type, payload FROM public.audit_outbox "
                    "WHERE idempotency_key = :key"
                ),
                {"key": registration_audit_idempotency_key(attempt_id)},
            )
            rows = outbox.all()
            assert len(rows) == 1
            assert rows[0].event_type == "PATIENT_REGISTRATION_SUCCESS"
            assert rows[0].payload["metadata"] == {"provider": "supabase"}
    finally:
        await _cleanup(factory, subject, attempt_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_same_attempt_finalized_replay_never_duplicates_graph_or_outbox() -> None:
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    subject = f"registration-replay-{uuid.uuid4().hex}"
    attempt_id = uuid.uuid4().hex
    try:
        async with factory() as db:
            first = await finalize_patient_registration(
                db, provider_subject=subject, attempt_id=attempt_id
            )
        async with factory() as db:
            second = await finalize_patient_registration(
                db, provider_subject=subject, attempt_id=attempt_id
            )
        assert first.patient_id == second.patient_id
        assert first.created is True
        assert second.created is False
        async with factory() as db:
            count = await db.scalar(
                text(
                    "SELECT count(*) FROM public.audit_outbox "
                    "WHERE idempotency_key = :key"
                ),
                {"key": registration_audit_idempotency_key(attempt_id)},
            )
            assert int(count or 0) == 1
    finally:
        await _cleanup(factory, subject, attempt_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_outbox_failure_rolls_back_patient_identity_and_record() -> None:
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    subject = f"registration-outbox-rollback-{uuid.uuid4().hex}"
    attempt_id = uuid.uuid4().hex
    try:
        async with factory() as db:
            before = int(
                await db.scalar(select(func.count()).select_from(Patient)) or 0
            )
        async with factory() as db:
            with patch(
                "app.services.patient_registration_service.enqueue_audit_event",
                new=AsyncMock(side_effect=RuntimeError("synthetic outbox failure")),
            ):
                with pytest.raises(RuntimeError, match="synthetic outbox failure"):
                    await finalize_patient_registration(
                        db, provider_subject=subject, attempt_id=attempt_id
                    )
        async with factory() as db:
            assert (
                int(await db.scalar(select(func.count()).select_from(Patient)) or 0)
                == before
            )
            assert (
                int(
                    await db.scalar(
                        select(func.count())
                        .select_from(PatientAuthIdentity)
                        .where(
                            PatientAuthIdentity.provider == "supabase",
                            PatientAuthIdentity.provider_subject == subject,
                        )
                    )
                    or 0
                )
                == 0
            )
            assert (
                int(
                    await db.scalar(
                        text(
                            "SELECT count(*) FROM public.audit_outbox "
                            "WHERE idempotency_key = :key"
                        ),
                        {"key": registration_audit_idempotency_key(attempt_id)},
                    )
                    or 0
                )
                == 0
            )
    finally:
        await _cleanup(factory, subject, attempt_id)
        await engine.dispose()
