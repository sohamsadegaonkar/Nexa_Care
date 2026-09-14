"""PostgreSQL qualification for Slice 10A patient search identifiers."""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.patient import Patient
from app.models.patient_auth_identity import PatientAuthIdentity
from app.models.patient_search_identifier import PatientSearchIdentifier
from app.services.patient_search_identifier_service import (
    PatientSearchIdentifierConflict,
    PatientSearchIdentifierNoMatch,
    PatientSearchIdentifierUnavailable,
    resolve_verified_phone_patient,
    revoke_active_identifiers_for_identity,
    synchronize_verified_phone_identifier,
)
from tests.helpers.qualification_infra import (
    create_disposable_database,
    drop_disposable_database,
    migrate_database_to_head,
    postgres_database_url,
)

pytestmark = pytest.mark.postgres

_HEAD = "20260914_patient_search_identifiers"
_DB_NAME = "nexa_qual_patient_search_identifiers"


def _database_url() -> str:
    return postgres_database_url(_DB_NAME)


@pytest.fixture(scope="module", autouse=True)
def _setup_database() -> None:
    url = asyncio.run(create_disposable_database(_DB_NAME))
    migrate_database_to_head(url, target_head=_HEAD)
    yield
    asyncio.run(drop_disposable_database(_DB_NAME))


@pytest.fixture(autouse=True)
def _keyring(monkeypatch) -> None:
    monkeypatch.setenv(
        "PATIENT_DISCOVERY_INDEX_HMAC_KEYS_JSON",
        json.dumps({"1": "a" * 48, "2": "b" * 48}),
    )
    monkeypatch.setenv("PATIENT_DISCOVERY_INDEX_ACTIVE_KEY_VERSION", "2")


@pytest.fixture(autouse=True)
async def _isolate_search_identifiers() -> None:
    """Prevent one key-rotation case from weakening or poisoning another."""

    engine = create_async_engine(_database_url())
    try:
        async with engine.begin() as conn:
            await conn.execute(delete(PatientSearchIdentifier))
        yield
    finally:
        async with engine.begin() as conn:
            await conn.execute(delete(PatientSearchIdentifier))
        await engine.dispose()


async def _create_authority(db) -> tuple[Patient, PatientAuthIdentity]:
    patient = Patient(is_deleted=False)
    db.add(patient)
    await db.flush()
    identity = PatientAuthIdentity(
        patient_id=patient.patient_uuid,
        provider="supabase",
        provider_subject=f"subject-{uuid.uuid4()}",
    )
    db.add(identity)
    await db.flush()
    return patient, identity


@pytest.mark.asyncio
async def test_verified_phone_is_stored_only_as_keyed_index_and_resolves() -> None:
    engine = create_async_engine(_database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    phone = "+919876543210"
    try:
        async with factory() as db:
            async with db.begin():
                patient, identity = await _create_authority(db)
                row = await synchronize_verified_phone_identifier(
                    db,
                    patient_id=patient.patient_uuid,
                    identity_id=identity.identity_id,
                    verified_phone=phone,
                )
                patient_id = patient.patient_uuid
                identity_id = identity.identity_id
                assert row.key_version == 2
                assert row.normalization_version == 1
                assert row.revoked_at is None
                assert phone not in row.value_hmac

        async with factory() as db:
            rows = list(
                (
                    await db.scalars(
                        select(PatientSearchIdentifier).where(
                            PatientSearchIdentifier.patient_id == patient_id
                        )
                    )
                ).all()
            )
            assert len(rows) == 1
            assert rows[0].identity_id == identity_id
            assert len(rows[0].value_hmac) == 64
            resolved, redirected = await resolve_verified_phone_patient(db, phone=phone)
            assert resolved.patient_uuid == patient_id
            assert redirected is False

            audit_rows = (
                await db.execute(
                    text(
                        "SELECT event_type, payload::text FROM public.audit_outbox "
                        "WHERE patient_id = :patient_id "
                        "AND event_type = 'PATIENT_SEARCH_IDENTIFIER_BOUND'"
                    ),
                    {"patient_id": str(patient_id)},
                )
            ).all()
            assert len(audit_rows) == 1
            assert phone not in audit_rows[0][1]
            assert "PHONE" in audit_rows[0][1]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_reverification_is_idempotent_and_phone_change_supersedes() -> None:
    engine = create_async_engine(_database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    first_phone = "+919876543211"
    second_phone = "+919876543212"
    try:
        async with factory() as db:
            async with db.begin():
                patient, identity = await _create_authority(db)
                patient_id = patient.patient_uuid
                identity_id = identity.identity_id
                first = await synchronize_verified_phone_identifier(
                    db,
                    patient_id=patient_id,
                    identity_id=identity_id,
                    verified_phone=first_phone,
                )
                replay = await synchronize_verified_phone_identifier(
                    db,
                    patient_id=patient_id,
                    identity_id=identity_id,
                    verified_phone=first_phone,
                )
                assert replay.identifier_id == first.identifier_id

        async with factory() as db:
            async with db.begin():
                replacement = await synchronize_verified_phone_identifier(
                    db,
                    patient_id=patient_id,
                    identity_id=identity_id,
                    verified_phone=second_phone,
                )
                assert replacement.revoked_at is None

        async with factory() as db:
            rows = list(
                (
                    await db.scalars(
                        select(PatientSearchIdentifier)
                        .where(PatientSearchIdentifier.patient_id == patient_id)
                        .order_by(PatientSearchIdentifier.created_at)
                    )
                ).all()
            )
            assert len(rows) == 2
            revoked = [row for row in rows if row.revoked_at is not None]
            active = [row for row in rows if row.revoked_at is None]
            assert len(revoked) == len(active) == 1
            assert revoked[0].revocation_reason == "SUPERSEDED"
            with pytest.raises(PatientSearchIdentifierNoMatch):
                await resolve_verified_phone_patient(db, phone=first_phone)
            resolved, _ = await resolve_verified_phone_patient(db, phone=second_phone)
            assert resolved.patient_uuid == patient_id

            events = list(
                (
                    await db.execute(
                        text(
                            "SELECT event_type FROM public.audit_outbox "
                            "WHERE patient_id = :patient_id "
                            "AND event_type LIKE 'PATIENT_SEARCH_IDENTIFIER_%'"
                        ),
                        {"patient_id": str(patient_id)},
                    )
                ).scalars()
            )
            assert events.count("PATIENT_SEARCH_IDENTIFIER_BOUND") == 2
            assert events.count("PATIENT_SEARCH_IDENTIFIER_SUPERSEDED") == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_same_phone_cannot_silently_move_to_another_patient(monkeypatch) -> None:
    engine = create_async_engine(_database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    phone = "+919876543213"
    try:
        monkeypatch.setenv(
            "PATIENT_DISCOVERY_INDEX_HMAC_KEYS_JSON",
            json.dumps({"1": "a" * 48}),
        )
        monkeypatch.setenv("PATIENT_DISCOVERY_INDEX_ACTIVE_KEY_VERSION", "1")
        async with factory() as db:
            async with db.begin():
                first_patient, first_identity = await _create_authority(db)
                first_patient_id = first_patient.patient_uuid
                await synchronize_verified_phone_identifier(
                    db,
                    patient_id=first_patient_id,
                    identity_id=first_identity.identity_id,
                    verified_phone=phone,
                )

        # Rotate to a new active key while retaining the old key for collision checks.
        monkeypatch.setenv(
            "PATIENT_DISCOVERY_INDEX_HMAC_KEYS_JSON",
            json.dumps({"1": "a" * 48, "2": "b" * 48}),
        )
        monkeypatch.setenv("PATIENT_DISCOVERY_INDEX_ACTIVE_KEY_VERSION", "2")
        async with factory() as db:
            async with db.begin():
                second_patient, second_identity = await _create_authority(db)
                with pytest.raises(PatientSearchIdentifierConflict):
                    await synchronize_verified_phone_identifier(
                        db,
                        patient_id=second_patient.patient_uuid,
                        identity_id=second_identity.identity_id,
                        verified_phone=phone,
                    )

        async with factory() as db:
            resolved, _ = await resolve_verified_phone_patient(db, phone=phone)
            assert resolved.patient_uuid == first_patient_id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_retiring_key_with_active_rows_fails_closed(monkeypatch) -> None:
    engine = create_async_engine(_database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    phone = "+919876543215"
    try:
        monkeypatch.setenv(
            "PATIENT_DISCOVERY_INDEX_HMAC_KEYS_JSON",
            json.dumps({"1": "a" * 48}),
        )
        monkeypatch.setenv("PATIENT_DISCOVERY_INDEX_ACTIVE_KEY_VERSION", "1")
        async with factory() as db:
            async with db.begin():
                patient, identity = await _create_authority(db)
                await synchronize_verified_phone_identifier(
                    db,
                    patient_id=patient.patient_uuid,
                    identity_id=identity.identity_id,
                    verified_phone=phone,
                )

        # Removing v1 before its active row is reverified/reindexed is unsafe.
        monkeypatch.setenv(
            "PATIENT_DISCOVERY_INDEX_HMAC_KEYS_JSON",
            json.dumps({"2": "b" * 48}),
        )
        monkeypatch.setenv("PATIENT_DISCOVERY_INDEX_ACTIVE_KEY_VERSION", "2")
        async with factory() as db:
            with pytest.raises(PatientSearchIdentifierUnavailable):
                await resolve_verified_phone_patient(db, phone=phone)

        async with factory() as db:
            async with db.begin():
                other_patient, other_identity = await _create_authority(db)
                with pytest.raises(PatientSearchIdentifierUnavailable):
                    await synchronize_verified_phone_identifier(
                        db,
                        patient_id=other_patient.patient_uuid,
                        identity_id=other_identity.identity_id,
                        verified_phone="+919876543216",
                    )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_identifier_binding(monkeypatch) -> None:
    engine = create_async_engine(_database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    phone = "+919876543217"
    patient_id = None
    try:
        async with factory() as db:
            async with db.begin():
                patient, identity = await _create_authority(db)
                patient_id = patient.patient_uuid

                async def _fail_audit(*args, **kwargs):
                    raise RuntimeError("synthetic audit failure")

                monkeypatch.setattr(
                    "app.services.patient_search_identifier_service.enqueue_audit_event",
                    _fail_audit,
                )
                with pytest.raises(RuntimeError, match="synthetic audit failure"):
                    await synchronize_verified_phone_identifier(
                        db,
                        patient_id=patient_id,
                        identity_id=identity.identity_id,
                        verified_phone=phone,
                    )

        async with factory() as db:
            rows = list(
                (
                    await db.scalars(
                        select(PatientSearchIdentifier).where(
                            PatientSearchIdentifier.patient_id == patient_id
                        )
                    )
                ).all()
            )
            assert rows == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_identity_revocation_removes_search_authority() -> None:
    engine = create_async_engine(_database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    phone = "+919876543214"
    try:
        async with factory() as db:
            async with db.begin():
                patient, identity = await _create_authority(db)
                await synchronize_verified_phone_identifier(
                    db,
                    patient_id=patient.patient_uuid,
                    identity_id=identity.identity_id,
                    verified_phone=phone,
                )
                identity_id = identity.identity_id
                patient_id = patient.patient_uuid

        async with factory() as db:
            async with db.begin():
                count = await revoke_active_identifiers_for_identity(
                    db, identity_id=identity_id
                )
                assert count == 1

        async with factory() as db:
            with pytest.raises(PatientSearchIdentifierNoMatch):
                await resolve_verified_phone_patient(db, phone=phone)
            events = list(
                (
                    await db.execute(
                        text(
                            "SELECT event_type FROM public.audit_outbox "
                            "WHERE patient_id = :patient_id "
                            "AND event_type = 'PATIENT_SEARCH_IDENTIFIER_REVOKED'"
                        ),
                        {"patient_id": str(patient_id)},
                    )
                ).scalars()
            )
            assert events == ["PATIENT_SEARCH_IDENTIFIER_REVOKED"]
    finally:
        await engine.dispose()
