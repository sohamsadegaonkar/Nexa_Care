"""PostgreSQL qualification for search-authority lifecycle integration."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.patient import Patient
from app.models.patient_auth_identity import PatientAuthIdentity
from app.models.patient_records import PatientRecord
from app.models.patient_search_identifier import PatientSearchIdentifier
from app.models.patient_tombstone import PatientTombstone
from app.services.patient_registration_recovery_authority import (
    RegistrationRecoveryCapability,
)
from app.services.patient_registration_recovery_service import (
    REPAIR_REBIND_MERGED_IDENTITY,
    inspect_patient_registration_recovery,
    repair_patient_registration_account,
)
from app.services.patient_registration_service import finalize_patient_registration
from app.services.patient_search_identifier_service import (
    PatientSearchIdentifierConflict,
    PatientSearchIdentifierNoMatch,
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


def _capability(inspection) -> RegistrationRecoveryCapability:
    assert inspection.repair_kind == REPAIR_REBIND_MERGED_IDENTITY
    return RegistrationRecoveryCapability(
        token=f"cap-{uuid.uuid4().hex}",
        patient_id=inspection.patient_id,
        provider_subject=inspection.provider_subject,
        repair_kind=inspection.repair_kind,
        graph_fingerprint=inspection.graph_fingerprint,
        issued_at="2026-09-15T00:00:00+00:00",
        expires_at="2099-09-15T00:05:00+00:00",
    )


async def _cleanup(factory, *patient_ids: uuid.UUID) -> None:
    ids = list(dict.fromkeys(patient_ids))
    if not ids:
        return
    string_ids = [str(item) for item in ids]
    async with factory() as db:
        await db.execute(
            text("DELETE FROM public.audit_outbox WHERE patient_id = ANY(:ids)"),
            {"ids": string_ids},
        )
        await db.execute(
            delete(PatientSearchIdentifier).where(
                PatientSearchIdentifier.patient_id.in_(ids)
            )
        )
        await db.execute(
            delete(PatientAuthIdentity).where(PatientAuthIdentity.patient_id.in_(ids))
        )
        await db.execute(delete(PatientRecord).where(PatientRecord.patient_id.in_(ids)))
        await db.execute(
            delete(PatientTombstone).where(
                (PatientTombstone.old_patient_uuid.in_(ids))
                | (PatientTombstone.canonical_patient_uuid.in_(ids))
            )
        )
        await db.execute(delete(Patient).where(Patient.patient_uuid.in_(ids)))
        await db.commit()


@pytest.mark.asyncio
async def test_merge_rebind_revokes_premerge_phone_search_authority(monkeypatch) -> None:
    _configure_index(monkeypatch)
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    subject = f"search-rebind-{uuid.uuid4().hex}"
    source_id: uuid.UUID | None = None
    canonical_id = uuid.uuid4()
    phone = "+919876543240"

    try:
        async with factory() as db:
            account = await finalize_patient_registration(
                db,
                provider_subject=subject,
                attempt_id=uuid.uuid4().hex,
            )
            source_id = uuid.UUID(account.patient_id)

        async with factory() as db:
            async with db.begin():
                identity = await db.scalar(
                    select(PatientAuthIdentity).where(
                        PatientAuthIdentity.provider == "supabase",
                        PatientAuthIdentity.provider_subject == subject,
                    )
                )
                assert identity is not None
                await synchronize_verified_phone_identifier(
                    db,
                    patient_id=source_id,
                    identity_id=identity.identity_id,
                    verified_phone=phone,
                )

        async with factory() as db:
            canonical = Patient(patient_uuid=canonical_id, is_deleted=False)
            db.add(canonical)
            db.add(PatientRecord(patient_id=canonical_id))
            db.add(
                PatientTombstone(
                    old_patient_uuid=source_id,
                    canonical_patient_uuid=canonical_id,
                    merged_by="qualification",
                    reason="duplicate identity",
                    evidence={"synthetic": True},
                )
            )
            await db.execute(
                update(Patient)
                .where(Patient.patient_uuid == source_id)
                .values(is_deleted=True, updated_at=datetime.now(timezone.utc))
            )
            await db.commit()

        async with factory() as db:
            inspection = await inspect_patient_registration_recovery(
                db, provider_subject=subject
            )
            assert inspection.repair_kind == REPAIR_REBIND_MERGED_IDENTITY

        async with factory() as db:
            repaired = await repair_patient_registration_account(
                db, capability=_capability(inspection)
            )
            assert repaired.patient_id == str(canonical_id)

        async with factory() as db:
            identity = await db.scalar(
                select(PatientAuthIdentity).where(
                    PatientAuthIdentity.provider == "supabase",
                    PatientAuthIdentity.provider_subject == subject,
                )
            )
            assert identity is not None and identity.patient_id == canonical_id
            row = await db.scalar(
                select(PatientSearchIdentifier).where(
                    PatientSearchIdentifier.identity_id == identity.identity_id
                )
            )
            assert row is not None
            assert row.revoked_at is not None
            assert row.revocation_reason == "IDENTITY_REBOUND"
            with pytest.raises(PatientSearchIdentifierNoMatch):
                await resolve_verified_phone_patient(db, phone=phone)
    finally:
        if source_id is not None:
            await _cleanup(factory, source_id, canonical_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_verified_phone_conflict_quarantines_old_and_new_identity_authority(
    monkeypatch,
) -> None:
    _configure_index(monkeypatch)
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    first_id: uuid.UUID | None = None
    second_id: uuid.UUID | None = None
    first_subject = f"search-conflict-a-{uuid.uuid4().hex}"
    second_subject = f"search-conflict-b-{uuid.uuid4().hex}"
    first_phone = "+919876543241"
    second_phone = "+919876543242"

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

        async with factory() as db:
            async with db.begin():
                second_identity = await db.scalar(
                    select(PatientAuthIdentity).where(
                        PatientAuthIdentity.provider_subject == second_subject
                    )
                )
                assert second_identity is not None
                with pytest.raises(PatientSearchIdentifierConflict):
                    await synchronize_verified_phone_identifier(
                        db,
                        patient_id=second_id,
                        identity_id=second_identity.identity_id,
                        verified_phone=first_phone,
                    )
                quarantined = await quarantine_verified_phone_conflict(
                    db,
                    identity_id=second_identity.identity_id,
                    verified_phone=first_phone,
                )
                assert quarantined == 2

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
            assert all(row.revoked_at is not None for row in rows)
            assert {row.revocation_reason for row in rows} == {"AUTHORITY_CONFLICT"}
            with pytest.raises(PatientSearchIdentifierNoMatch):
                await resolve_verified_phone_patient(db, phone=first_phone)
            with pytest.raises(PatientSearchIdentifierNoMatch):
                await resolve_verified_phone_patient(db, phone=second_phone)
    finally:
        if first_id is not None and second_id is not None:
            await _cleanup(factory, first_id, second_id)
        await engine.dispose()
