"""PostgreSQL rollback proof for Slice 10A search-authority quarantine."""

from __future__ import annotations

import json
import os
import uuid

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.patient import Patient
from app.models.patient_auth_identity import PatientAuthIdentity
from app.models.patient_records import PatientRecord
from app.models.patient_search_identifier import PatientSearchIdentifier
from app.services.patient_registration_service import finalize_patient_registration
from app.services.patient_search_identifier_service import (
    quarantine_verified_phone_conflict,
    resolve_verified_phone_patient,
    synchronize_verified_phone_identifier,
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


def _configure_index(monkeypatch) -> None:
    monkeypatch.setenv(
        "PATIENT_DISCOVERY_INDEX_HMAC_KEYS_JSON",
        json.dumps({"1": "a" * 48, "2": "b" * 48}),
    )
    monkeypatch.setenv("PATIENT_DISCOVERY_INDEX_ACTIVE_KEY_VERSION", "2")


async def _cleanup(factory, patient_ids: list[uuid.UUID]) -> None:
    string_ids = [str(item) for item in patient_ids]
    async with factory() as db:
        await db.execute(
            text("DELETE FROM public.audit_outbox WHERE patient_id = ANY(:ids)"),
            {"ids": string_ids},
        )
        await db.execute(
            delete(PatientSearchIdentifier).where(
                PatientSearchIdentifier.patient_id.in_(patient_ids)
            )
        )
        await db.execute(
            delete(PatientAuthIdentity).where(
                PatientAuthIdentity.patient_id.in_(patient_ids)
            )
        )
        await db.execute(
            delete(PatientRecord).where(PatientRecord.patient_id.in_(patient_ids))
        )
        await db.execute(delete(Patient).where(Patient.patient_uuid.in_(patient_ids)))
        await db.commit()


@pytest.mark.asyncio
async def test_conflict_quarantine_audit_failure_rolls_back_all_revocations(
    monkeypatch,
) -> None:
    _configure_index(monkeypatch)
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    patient_ids: list[uuid.UUID] = []
    first_subject = f"search-atomic-a-{uuid.uuid4().hex}"
    second_subject = f"search-atomic-b-{uuid.uuid4().hex}"
    first_phone = "+919876543245"
    second_phone = "+919876543246"

    try:
        async with factory() as db:
            first = await finalize_patient_registration(
                db,
                provider_subject=first_subject,
                attempt_id=uuid.uuid4().hex,
            )
            second = await finalize_patient_registration(
                db,
                provider_subject=second_subject,
                attempt_id=uuid.uuid4().hex,
            )
            first_id = uuid.UUID(first.patient_id)
            second_id = uuid.UUID(second.patient_id)
            patient_ids.extend([first_id, second_id])

        async with factory() as db:
            async with db.begin():
                first_identity = await db.scalar(
                    select(PatientAuthIdentity).where(
                        PatientAuthIdentity.provider_subject == first_subject
                    )
                )
                second_identity = await db.scalar(
                    select(PatientAuthIdentity).where(
                        PatientAuthIdentity.provider_subject == second_subject
                    )
                )
                assert first_identity is not None and second_identity is not None
                await synchronize_verified_phone_identifier(
                    db,
                    patient_id=first_id,
                    identity_id=first_identity.identity_id,
                    verified_phone=first_phone,
                )
                await synchronize_verified_phone_identifier(
                    db,
                    patient_id=second_id,
                    identity_id=second_identity.identity_id,
                    verified_phone=second_phone,
                )
                second_identity_id = second_identity.identity_id

        async def _fail_audit(*args, **kwargs):
            raise RuntimeError("synthetic conflict-quarantine audit failure")

        monkeypatch.setattr(
            "app.services.patient_search_identifier_service.enqueue_audit_event",
            _fail_audit,
        )
        async with factory() as db:
            with pytest.raises(
                RuntimeError, match="synthetic conflict-quarantine audit failure"
            ):
                async with db.begin():
                    await quarantine_verified_phone_conflict(
                        db,
                        identity_id=second_identity_id,
                        verified_phone=first_phone,
                    )

        async with factory() as db:
            rows = list(
                (
                    await db.scalars(
                        select(PatientSearchIdentifier).where(
                            PatientSearchIdentifier.patient_id.in_([first_id, second_id])
                        )
                    )
                ).all()
            )
            assert len(rows) == 2
            assert all(row.revoked_at is None for row in rows)
            assert all(row.revocation_reason is None for row in rows)
            first_resolved, _ = await resolve_verified_phone_patient(
                db, phone=first_phone
            )
            second_resolved, _ = await resolve_verified_phone_patient(
                db, phone=second_phone
            )
            assert first_resolved.patient_uuid == first_id
            assert second_resolved.patient_uuid == second_id
    finally:
        if patient_ids:
            await _cleanup(factory, patient_ids)
        await engine.dispose()
