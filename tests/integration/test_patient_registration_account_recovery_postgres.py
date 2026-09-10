"""Real PostgreSQL qualification for patient registration-account recovery."""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.erasure_tombstone import PatientErasureTombstone
from app.models.patient import Patient
from app.models.patient_auth_identity import PatientAuthIdentity
from app.models.patient_records import PatientRecord
from app.models.patient_tombstone import PatientTombstone
from app.services.patient_registration_recovery_authority import (
    RegistrationRecoveryCapability,
)
from app.services.patient_registration_recovery_service import (
    REGISTRATION_RECOVERY_STATE_CHANGED,
    REPAIR_REBIND_MERGED_IDENTITY,
    REPAIR_RESTORE_RECORD,
    PatientRegistrationRecoveryError,
    audit_registration_recovery_required,
    inspect_patient_registration_recovery,
    repair_patient_registration_account,
)
from app.services.patient_registration_service import finalize_patient_registration


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


def _capability(inspection, *, token: str) -> RegistrationRecoveryCapability:
    assert inspection.repair_kind is not None
    return RegistrationRecoveryCapability(
        token=token,
        patient_id=inspection.patient_id,
        provider_subject=inspection.provider_subject,
        repair_kind=inspection.repair_kind,
        graph_fingerprint=inspection.graph_fingerprint,
        issued_at="2026-09-10T00:00:00+00:00",
        expires_at="2099-09-10T00:05:00+00:00",
    )


def _required_key(attempt_id: str) -> str:
    return "patient-registration-recovery-required:" + hashlib.sha256(
        attempt_id.encode("utf-8")
    ).hexdigest()


def _completed_key(token: str) -> str:
    return "patient-registration-recovery-completed:" + hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()


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
            delete(PatientErasureTombstone).where(
                PatientErasureTombstone.patient_ref.in_(string_ids)
            )
        )
        await db.execute(
            delete(PatientAuthIdentity).where(
                PatientAuthIdentity.patient_id.in_(ids)
            )
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
async def test_missing_record_anchor_is_repaired_and_audited_transactionally() -> None:
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    subject = f"registration-account-recovery-record-{uuid.uuid4().hex}"
    registration_attempt = uuid.uuid4().hex
    recovery_attempt = uuid.uuid4().hex
    capability_token = f"cap-{uuid.uuid4().hex}"
    patient_id: uuid.UUID | None = None

    try:
        async with factory() as db:
            account = await finalize_patient_registration(
                db, provider_subject=subject, attempt_id=registration_attempt
            )
            patient_id = uuid.UUID(account.patient_id)

        async with factory() as db:
            await db.execute(
                delete(PatientRecord).where(PatientRecord.patient_id == patient_id)
            )
            await db.commit()

        async with factory() as db:
            inspection = await inspect_patient_registration_recovery(
                db, provider_subject=subject
            )
            assert inspection.repairable
            assert inspection.repair_kind == REPAIR_RESTORE_RECORD
            await audit_registration_recovery_required(
                db, inspection=inspection, attempt_id=recovery_attempt
            )
            await db.commit()

        async with factory() as db:
            required_event = (
                await db.execute(
                    text(
                        """SELECT event_type, payload FROM public.audit_outbox
                           WHERE idempotency_key = :key"""
                    ),
                    {"key": _required_key(recovery_attempt)},
                )
            ).one()
            assert required_event.event_type == "PATIENT_REGISTRATION_RECOVERY_REQUIRED"
            assert required_event.payload["status"] == "REQUIRED"
            assert required_event.payload["metadata"]["repair_kind"] == REPAIR_RESTORE_RECORD

        async with factory() as db:
            result = await repair_patient_registration_account(
                db, capability=_capability(inspection, token=capability_token)
            )
            assert result.patient_id == str(patient_id)
            assert result.repair_kind == REPAIR_RESTORE_RECORD

        async with factory() as db:
            records = list(
                (
                    await db.scalars(
                        select(PatientRecord).where(PatientRecord.patient_id == patient_id)
                    )
                ).all()
            )
            assert len(records) == 1
            completed = (
                await db.execute(
                    text(
                        """SELECT event_type, payload FROM public.audit_outbox
                           WHERE idempotency_key = :key"""
                    ),
                    {"key": _completed_key(capability_token)},
                )
            ).one()
            assert completed.event_type == "PATIENT_REGISTRATION_RECOVERY_COMPLETED"
            assert completed.payload["status"] == "SUCCESS"
            assert completed.payload["metadata"]["repair_kind"] == REPAIR_RESTORE_RECORD
    finally:
        if patient_id is not None:
            await _cleanup(factory, patient_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_merge_tombstone_rebinds_identity_but_never_undeletes_old_patient() -> None:
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    subject = f"registration-account-recovery-merge-{uuid.uuid4().hex}"
    registration_attempt = uuid.uuid4().hex
    source_id: uuid.UUID | None = None
    canonical_id = uuid.uuid4()

    try:
        async with factory() as db:
            account = await finalize_patient_registration(
                db, provider_subject=subject, attempt_id=registration_attempt
            )
            source_id = uuid.UUID(account.patient_id)

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
            assert inspection.repairable
            assert inspection.repair_kind == REPAIR_REBIND_MERGED_IDENTITY
            assert inspection.target_patient_id == str(canonical_id)

        async with factory() as db:
            result = await repair_patient_registration_account(
                db, capability=_capability(inspection, token=f"cap-{uuid.uuid4().hex}")
            )
            assert result.patient_id == str(canonical_id)

        async with factory() as db:
            source = await db.get(Patient, source_id)
            identity = await db.scalar(
                select(PatientAuthIdentity).where(
                    PatientAuthIdentity.provider == "supabase",
                    PatientAuthIdentity.provider_subject == subject,
                )
            )
            assert source is not None and source.is_deleted is True
            assert identity is not None and identity.patient_id == canonical_id
            assert identity.revoked_at is None
    finally:
        if source_id is not None:
            await _cleanup(factory, source_id, canonical_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_revoked_or_erased_identity_is_manual_review_and_never_repaired() -> None:
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)

    for state in ("revoked", "erased"):
        subject = f"registration-account-recovery-{state}-{uuid.uuid4().hex}"
        registration_attempt = uuid.uuid4().hex
        patient_id: uuid.UUID | None = None
        try:
            async with factory() as db:
                account = await finalize_patient_registration(
                    db, provider_subject=subject, attempt_id=registration_attempt
                )
                patient_id = uuid.UUID(account.patient_id)

            async with factory() as db:
                if state == "revoked":
                    await db.execute(
                        update(PatientAuthIdentity)
                        .where(
                            PatientAuthIdentity.provider == "supabase",
                            PatientAuthIdentity.provider_subject == subject,
                        )
                        .values(revoked_at=datetime.now(timezone.utc))
                    )
                else:
                    db.add(
                        PatientErasureTombstone(
                            patient_ref=str(patient_id),
                            tenant_id=None,
                            status="access_blocked",
                            assurance_level="active_access_blocked",
                            wrapping_key_type="shared",
                        )
                    )
                await db.commit()

            async with factory() as db:
                inspection = await inspect_patient_registration_recovery(
                    db, provider_subject=subject
                )
                assert inspection.disposition == "manual_review"
                assert not inspection.repairable
                expected = "IDENTITY_REVOKED" if state == "revoked" else "ERASURE_STATE_PRESENT"
                assert inspection.reason_code == expected
        finally:
            if patient_id is not None:
                await _cleanup(factory, patient_id)

    await engine.dispose()


@pytest.mark.asyncio
async def test_multiple_source_identities_and_canonical_identity_conflict_require_manual_review() -> None:
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    source_id = uuid.uuid4()
    canonical_id = uuid.uuid4()
    subject = f"registration-account-recovery-conflict-{uuid.uuid4().hex}"

    try:
        async with factory() as db:
            db.add_all(
                [
                    Patient(patient_uuid=source_id, is_deleted=False),
                    Patient(patient_uuid=canonical_id, is_deleted=False),
                ]
            )
            await db.flush()
            db.add_all(
                [
                    PatientRecord(patient_id=source_id),
                    PatientRecord(patient_id=canonical_id),
                    PatientAuthIdentity(
                        patient_id=source_id,
                        provider="supabase",
                        provider_subject=subject,
                    ),
                    PatientAuthIdentity(
                        patient_id=source_id,
                        provider="supabase",
                        provider_subject=f"secondary-{uuid.uuid4().hex}",
                    ),
                ]
            )
            await db.commit()

        async with factory() as db:
            inspection = await inspect_patient_registration_recovery(
                db, provider_subject=subject
            )
            assert inspection.disposition == "manual_review"
            assert inspection.reason_code == "MULTIPLE_SOURCE_IDENTITIES"

        async with factory() as db:
            secondary = await db.scalar(
                select(PatientAuthIdentity).where(
                    PatientAuthIdentity.patient_id == source_id,
                    PatientAuthIdentity.provider_subject != subject,
                )
            )
            await db.delete(secondary)
            await db.execute(
                update(Patient).where(Patient.patient_uuid == source_id).values(is_deleted=True)
            )
            db.add(
                PatientTombstone(
                    old_patient_uuid=source_id,
                    canonical_patient_uuid=canonical_id,
                    merged_by="qualification",
                    reason="duplicate",
                )
            )
            db.add(
                PatientAuthIdentity(
                    patient_id=canonical_id,
                    provider="supabase",
                    provider_subject=f"canonical-{uuid.uuid4().hex}",
                )
            )
            await db.commit()

        async with factory() as db:
            inspection = await inspect_patient_registration_recovery(
                db, provider_subject=subject
            )
            assert inspection.disposition == "manual_review"
            assert inspection.reason_code == "CANONICAL_IDENTITY_CONFLICT"
    finally:
        await _cleanup(factory, source_id, canonical_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_graph_change_after_capability_issuance_fails_closed_without_repair_audit() -> None:
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    subject = f"registration-account-recovery-drift-{uuid.uuid4().hex}"
    registration_attempt = uuid.uuid4().hex
    capability_token = f"cap-{uuid.uuid4().hex}"
    patient_id: uuid.UUID | None = None

    try:
        async with factory() as db:
            account = await finalize_patient_registration(
                db, provider_subject=subject, attempt_id=registration_attempt
            )
            patient_id = uuid.UUID(account.patient_id)
        async with factory() as db:
            await db.execute(delete(PatientRecord).where(PatientRecord.patient_id == patient_id))
            await db.commit()
        async with factory() as db:
            inspection = await inspect_patient_registration_recovery(
                db, provider_subject=subject
            )
            assert inspection.repair_kind == REPAIR_RESTORE_RECORD

        async with factory() as db:
            db.add(PatientRecord(patient_id=patient_id))
            await db.commit()

        async with factory() as db:
            with pytest.raises(PatientRegistrationRecoveryError) as exc_info:
                await repair_patient_registration_account(
                    db, capability=_capability(inspection, token=capability_token)
                )
            assert exc_info.value.code == REGISTRATION_RECOVERY_STATE_CHANGED

        async with factory() as db:
            record_count = await db.scalar(
                text("SELECT count(*) FROM public.patient_records WHERE patient_id = :pid"),
                {"pid": str(patient_id)},
            )
            completion = await db.scalar(
                text("SELECT id FROM public.audit_outbox WHERE idempotency_key = :key"),
                {"key": _completed_key(capability_token)},
            )
            assert record_count == 1
            assert completion is None
    finally:
        if patient_id is not None:
            await _cleanup(factory, patient_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_unexplained_soft_delete_is_never_undeleted_by_recovery() -> None:
    engine = create_async_engine(_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    subject = f"registration-account-recovery-deleted-{uuid.uuid4().hex}"
    registration_attempt = uuid.uuid4().hex
    patient_id: uuid.UUID | None = None

    try:
        async with factory() as db:
            account = await finalize_patient_registration(
                db, provider_subject=subject, attempt_id=registration_attempt
            )
            patient_id = uuid.UUID(account.patient_id)
        async with factory() as db:
            await db.execute(
                update(Patient).where(Patient.patient_uuid == patient_id).values(is_deleted=True)
            )
            await db.commit()
        async with factory() as db:
            inspection = await inspect_patient_registration_recovery(
                db, provider_subject=subject
            )
            assert inspection.disposition == "manual_review"
            assert inspection.reason_code == "DELETED_PATIENT_WITHOUT_MERGE_TOMBSTONE"
            assert not inspection.repairable
        async with factory() as db:
            patient = await db.get(Patient, patient_id)
            assert patient is not None and patient.is_deleted is True
    finally:
        if patient_id is not None:
            await _cleanup(factory, patient_id)
        await engine.dispose()
