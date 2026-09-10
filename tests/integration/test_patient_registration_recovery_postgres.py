"""PostgreSQL qualification for explicit patient registration recovery states."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.patient import Patient
from app.models.patient_auth_identity import PatientAuthIdentity
from app.models.patient_records import PatientRecord
from app.services.patient_registration_service import (
    REGISTRATION_RECOVERY_REQUIRED,
    PatientRegistrationError,
    finalize_patient_registration,
    recover_patient_registration_for_attempt,
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


async def _cleanup(factory, *, patient_id, attempt_ids: list[str]) -> None:
    async with factory() as db:
        for attempt_id in attempt_ids:
            await db.execute(
                text("DELETE FROM public.audit_outbox WHERE idempotency_key = :key"),
                {"key": registration_audit_idempotency_key(attempt_id)},
            )
        await db.execute(
            delete(PatientRecord).where(PatientRecord.patient_id == patient_id)
        )
        await db.execute(
            delete(PatientAuthIdentity).where(
                PatientAuthIdentity.patient_id == patient_id
            )
        )
        await db.execute(
            delete(Patient).where(Patient.patient_uuid == patient_id)
        )
        await db.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "historical_state", ["revoked_identity", "deleted_patient", "missing_record"]
)
async def test_historical_registration_graph_requires_explicit_recovery(
    historical_state: str,
) -> None:
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    subject = f"registration-recovery-{historical_state}-{uuid.uuid4().hex}"
    original_attempt = uuid.uuid4().hex
    fresh_attempt = uuid.uuid4().hex
    patient_id = None

    try:
        async with factory() as db:
            account = await finalize_patient_registration(
                db,
                provider_subject=subject,
                attempt_id=original_attempt,
            )
            patient_id = uuid.UUID(account.patient_id)

        async with factory() as db:
            if historical_state == "revoked_identity":
                await db.execute(
                    update(PatientAuthIdentity)
                    .where(
                        PatientAuthIdentity.provider == "supabase",
                        PatientAuthIdentity.provider_subject == subject,
                    )
                    .values(revoked_at=datetime.now(timezone.utc))
                )
            elif historical_state == "deleted_patient":
                await db.execute(
                    update(Patient)
                    .where(Patient.patient_uuid == patient_id)
                    .values(is_deleted=True)
                )
            else:
                await db.execute(
                    delete(PatientRecord).where(
                        PatientRecord.patient_id == patient_id
                    )
                )
            await db.commit()

        async with factory() as db:
            with pytest.raises(PatientRegistrationError) as exc_info:
                await finalize_patient_registration(
                    db,
                    provider_subject=subject,
                    attempt_id=fresh_attempt,
                )
        assert exc_info.value.code == REGISTRATION_RECOVERY_REQUIRED
    finally:
        if patient_id is not None:
            await _cleanup(
                factory,
                patient_id=patient_id,
                attempt_ids=[original_attempt, fresh_attempt],
            )
        await engine.dispose()


@pytest.mark.asyncio
async def test_same_attempt_durable_graph_with_missing_identity_requires_recovery() -> None:
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    subject = f"registration-recovery-missing-identity-{uuid.uuid4().hex}"
    attempt_id = uuid.uuid4().hex
    patient_id = None

    try:
        async with factory() as db:
            account = await finalize_patient_registration(
                db,
                provider_subject=subject,
                attempt_id=attempt_id,
            )
            patient_id = uuid.UUID(account.patient_id)

        async with factory() as db:
            await db.execute(
                delete(PatientAuthIdentity).where(
                    PatientAuthIdentity.patient_id == patient_id,
                    PatientAuthIdentity.provider == "supabase",
                )
            )
            await db.commit()

        async with factory() as db:
            with pytest.raises(PatientRegistrationError) as exc_info:
                await recover_patient_registration_for_attempt(
                    db,
                    attempt_id=attempt_id,
                    patient_id=str(patient_id),
                )
        assert exc_info.value.code == REGISTRATION_RECOVERY_REQUIRED
    finally:
        if patient_id is not None:
            await _cleanup(factory, patient_id=patient_id, attempt_ids=[attempt_id])
        await engine.dispose()


@pytest.mark.asyncio
async def test_mismatched_attempt_evidence_cannot_adopt_complete_account() -> None:
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    subject = f"registration-recovery-attempt-mismatch-{uuid.uuid4().hex}"
    original_attempt = uuid.uuid4().hex
    mismatched_attempt = uuid.uuid4().hex
    patient_id = None

    try:
        async with factory() as db:
            account = await finalize_patient_registration(
                db,
                provider_subject=subject,
                attempt_id=original_attempt,
            )
            patient_id = uuid.UUID(account.patient_id)

        async with factory() as db:
            with pytest.raises(PatientRegistrationError) as exc_info:
                await recover_patient_registration_for_attempt(
                    db,
                    attempt_id=mismatched_attempt,
                    patient_id=str(patient_id),
                )
        assert exc_info.value.code == REGISTRATION_RECOVERY_REQUIRED
    finally:
        if patient_id is not None:
            await _cleanup(
                factory,
                patient_id=patient_id,
                attempt_ids=[original_attempt, mismatched_attempt],
            )
        await engine.dispose()


@pytest.mark.asyncio
async def test_same_attempt_recovery_rejects_multiple_supabase_identities() -> None:
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    subject = f"registration-recovery-primary-{uuid.uuid4().hex}"
    second_subject = f"registration-recovery-secondary-{uuid.uuid4().hex}"
    attempt_id = uuid.uuid4().hex
    patient_id = None

    try:
        async with factory() as db:
            account = await finalize_patient_registration(
                db,
                provider_subject=subject,
                attempt_id=attempt_id,
            )
            patient_id = uuid.UUID(account.patient_id)

        async with factory() as db:
            db.add(
                PatientAuthIdentity(
                    patient_id=patient_id,
                    provider="supabase",
                    provider_subject=second_subject,
                )
            )
            await db.commit()

        async with factory() as db:
            with pytest.raises(PatientRegistrationError) as exc_info:
                await recover_patient_registration_for_attempt(
                    db,
                    attempt_id=attempt_id,
                    patient_id=str(patient_id),
                )
        assert exc_info.value.code == REGISTRATION_RECOVERY_REQUIRED
    finally:
        if patient_id is not None:
            await _cleanup(factory, patient_id=patient_id, attempt_ids=[attempt_id])
        await engine.dispose()
