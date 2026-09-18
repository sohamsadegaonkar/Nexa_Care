from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.clinical_session_gate import TreatmentSessionV1Authority
from app.security.audit_context import AuditContext, AuditDomain
from app.security.clinical_access_policy import (
    CLINICAL_ACCESS_POLICY_VERSION,
    ClinicalAccessOperation,
)
from app.services import treatment_vitals as treatment_vitals_service


def _database_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    if not url.startswith(("postgresql+asyncpg://", "postgresql://")):
        pytest.skip("TEST_DATABASE_URL does not identify PostgreSQL")
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_treatment_vitals_encounter_schema_is_installed():
    engine = create_async_engine(_database_url())
    try:
        async with engine.connect() as connection:
            column = (
                await connection.execute(
                    text(
                        """
                        SELECT is_nullable
                        FROM information_schema.columns
                        WHERE table_name = 'patient_vitals'
                          AND column_name = 'encounter_id'
                        """
                    )
                )
            ).first()
            assert column is not None
            assert column.is_nullable == "YES"

            constraint = (
                await connection.execute(
                    text(
                        """
                        SELECT pg_get_constraintdef(oid) AS definition
                        FROM pg_constraint
                        WHERE conname = 'fk_patient_vitals_encounter'
                          AND conrelid = 'patient_vitals'::regclass
                        """
                    )
                )
            ).first()
            assert constraint is not None
            assert "clinical_encounters(encounter_id)" in constraint.definition
            assert "ON DELETE RESTRICT" in constraint.definition

            index = await connection.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM pg_indexes
                    WHERE tablename = 'patient_vitals'
                      AND indexname = 'ix_patient_vitals_encounter_id'
                    """
                )
            )
            assert index == 1
    finally:
        await engine.dispose()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_treatment_vitals_encounter_fk_rejects_unknown_encounter():
    engine = create_async_engine(_database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            with pytest.raises(IntegrityError):
                await db.execute(
                    text(
                        """
                        INSERT INTO patient_vitals (
                            id, patient_id, encounter_id, type, value, unit,
                            recorded_at, source, confidence, risk_level,
                            source_document_id
                        ) VALUES (
                            :id, :patient_id, :encounter_id, 'HR', '72', 'bpm',
                            :recorded_at, 'manual', NULL, 'LOW_RISK', NULL
                        )
                        """
                    ),
                    {
                        "id": uuid.uuid4(),
                        "patient_id": uuid.uuid4(),
                        "encounter_id": uuid.uuid4(),
                        "recorded_at": datetime.now(timezone.utc),
                    },
                )
                await db.flush()
            await db.rollback()
    finally:
        await engine.dispose()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_treatment_vitals_migration_preserves_nullable_legacy_rows():
    engine = create_async_engine(_database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    row_id = uuid.uuid4()
    try:
        async with factory() as db:
            await db.execute(
                text(
                    """
                    INSERT INTO patient_vitals (
                        id, patient_id, encounter_id, type, value, unit,
                        recorded_at, source, confidence, risk_level,
                        source_document_id
                    ) VALUES (
                        :id, :patient_id, NULL, 'HR', '72', 'bpm',
                        :recorded_at, 'manual', NULL, 'LOW_RISK', NULL
                    )
                    """
                ),
                {
                    "id": row_id,
                    "patient_id": uuid.uuid4(),
                    "recorded_at": datetime.now(timezone.utc),
                },
            )
            await db.flush()
            encounter_id = await db.scalar(
                text(
                    "SELECT encounter_id FROM patient_vitals WHERE id = :id"
                ),
                {"id": row_id},
            )
            assert encounter_id is None
            await db.rollback()
    finally:
        await engine.dispose()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_treatment_vitals_same_key_concurrency_creates_one_clinical_fact(
    monkeypatch,
):
    """Two concurrent identical attempts converge on one durable logical write."""

    engine = create_async_engine(_database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)

    patient_id = uuid.uuid4()
    hospital_id = uuid.uuid4()
    provider_id = uuid.uuid4()
    session_id = uuid.uuid4()
    request_id = uuid.uuid4()
    encounter_id = uuid.uuid4()
    idempotency_key = f"vitals-concurrent-{uuid.uuid4().hex[:16]}"
    now = datetime.now(timezone.utc)
    token_hash = uuid.uuid4().hex + uuid.uuid4().hex
    binding_hash = uuid.uuid4().hex + uuid.uuid4().hex

    authority = TreatmentSessionV1Authority(
        session_id=session_id,
        request_id=request_id,
        patient_id=patient_id,
        provider_id=provider_id,
        hospital_id=hospital_id,
        purpose="treatment",
        allowed_operations=(ClinicalAccessOperation.WRITE_VITALS.value,),
        required_operation=ClinicalAccessOperation.WRITE_VITALS,
        provider_session_binding_hash=binding_hash,
        policy_version=CLINICAL_ACCESS_POLICY_VERSION,
        token_hash=token_hash,
        issued_at=now,
        expires_at=now + timedelta(minutes=10),
        encounter_id=encounter_id,
    )
    observation = treatment_vitals_service.heart_rate_observation(
        beats_per_minute=72,
        recorded_at=now,
    )
    audit_context = AuditContext.for_hospital(
        hospital_id=str(hospital_id),
        domain=AuditDomain.PATIENT_RECORD,
    )

    async def _qualified_lock(*, db, authority, required_operation):
        del db
        assert required_operation is ClinicalAccessOperation.WRITE_VITALS
        assert authority.encounter_id == encounter_id
        return SimpleNamespace(encounter_id=encounter_id)

    monkeypatch.setattr(
        treatment_vitals_service,
        "lock_treatment_write_authority",
        _qualified_lock,
    )

    try:
        async with factory() as db:
            await db.execute(
                text(
                    """
                    INSERT INTO patients
                        (patient_uuid, public_patient_id,
                         consent_assurance_policy, is_deleted)
                    VALUES (:patient_id, :public_id, 'STANDARD', false)
                    """
                ),
                {
                    "patient_id": patient_id,
                    "public_id": f"NC-{uuid.uuid4().hex[:24].upper()}",
                },
            )
            await db.execute(
                text(
                    """
                    INSERT INTO hospital_registry
                        (id, facility_code, legal_name, display_name,
                         country_code, is_active)
                    VALUES (
                        :hospital_id, :facility_code,
                        'Treatment Vitals Qualification Hospital',
                        'Treatment Vitals Qualification Hospital',
                        'IN', true
                    )
                    """
                ),
                {
                    "hospital_id": hospital_id,
                    "facility_code": f"VITAL-{uuid.uuid4().hex[:12]}",
                },
            )
            await db.execute(
                text(
                    """
                    INSERT INTO provider_identity
                        (id, provider_uid, hospital_id, role, status, is_active)
                    VALUES (
                        :provider_id, :provider_uid, :hospital_id,
                        'provider', 'active', true
                    )
                    """
                ),
                {
                    "provider_id": provider_id,
                    "provider_uid": f"vital-{uuid.uuid4().hex[:16]}",
                    "hospital_id": hospital_id,
                },
            )
            await db.execute(
                text(
                    """
                    INSERT INTO clinical_access_sessions (
                        session_id, patient_id, provider_id, hospital_id,
                        consent_request_id, token_hash, purpose, scope,
                        allowed_operations, provider_session_binding_hash,
                        policy_version, issued_at, expires_at, status,
                        encounter_id
                    ) VALUES (
                        :session_id, :patient_id, :provider_id, :hospital_id,
                        :request_id, :token_hash, 'treatment', 'treatment',
                        CAST(:operations AS JSONB), :binding_hash,
                        :policy_version, :issued_at, :expires_at,
                        'ACTIVE', :encounter_id
                    )
                    """
                ),
                {
                    "session_id": session_id,
                    "patient_id": patient_id,
                    "provider_id": provider_id,
                    "hospital_id": hospital_id,
                    "request_id": str(request_id),
                    "token_hash": token_hash,
                    "operations": '["WRITE_VITALS"]',
                    "binding_hash": binding_hash,
                    "policy_version": CLINICAL_ACCESS_POLICY_VERSION,
                    "issued_at": now,
                    "expires_at": authority.expires_at,
                    "encounter_id": str(encounter_id),
                },
            )
            await db.execute(
                text(
                    """
                    INSERT INTO clinical_encounters (
                        encounter_id, clinical_session_id, patient_id,
                        provider_id, hospital_id
                    ) VALUES (
                        :encounter_id, :session_id, :patient_id,
                        :provider_id, :hospital_id
                    )
                    """
                ),
                {
                    "encounter_id": encounter_id,
                    "session_id": session_id,
                    "patient_id": patient_id,
                    "provider_id": provider_id,
                    "hospital_id": hospital_id,
                },
            )
            await db.commit()

        async def _attempt():
            async with factory() as db:
                result = await treatment_vitals_service.stage_treatment_vital_write(
                    db=db,
                    authority=authority,
                    observation=observation,
                    idempotency_key=idempotency_key,
                    audit_context=audit_context,
                )
                await db.commit()
                return result

        first, second = await asyncio.gather(_attempt(), _attempt())

        assert first.record_id == second.record_id
        assert first.encounter_id == second.encounter_id == encounter_id
        assert sorted(
            [first.idempotent_replay, second.idempotent_replay]
        ) == [False, True]

        async with factory() as db:
            vital_count = await db.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM patient_vitals
                    WHERE patient_id = :patient_id
                      AND encounter_id = :encounter_id
                      AND type = 'HR'
                      AND value = '72'
                      AND unit = 'bpm'
                    """
                ),
                {
                    "patient_id": patient_id,
                    "encounter_id": encounter_id,
                },
            )
            timeline_count = await db.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM timeline_events
                    WHERE patient_id = :patient_id
                      AND event_type = 'VITALS'
                      AND event_ref_id = :record_id
                    """
                ),
                {
                    "patient_id": patient_id,
                    "record_id": first.record_id,
                },
            )
            idempotency_count = await db.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM public.mutation_idempotency
                    WHERE tenant_id = :hospital_id
                      AND operation = 'treatment.write_vitals.v1'
                      AND idempotency_key = :idempotency_key
                      AND response_status = 200
                    """
                ),
                {
                    "hospital_id": str(hospital_id),
                    "idempotency_key": idempotency_key,
                },
            )
            audit_count = await db.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM public.audit_outbox
                    WHERE patient_id = :patient_id
                      AND event_type = 'PATIENT_RECORD_APPEND_SUCCESS'
                    """
                ),
                {"patient_id": str(patient_id)},
            )

            assert vital_count == 1
            assert timeline_count == 1
            assert idempotency_count == 1
            assert audit_count == 1
    finally:
        async with factory() as db:
            await db.execute(
                text(
                    "DELETE FROM public.audit_outbox "
                    "WHERE patient_id = :patient_id"
                ),
                {"patient_id": str(patient_id)},
            )
            await db.execute(
                text(
                    """
                    DELETE FROM public.mutation_idempotency
                    WHERE tenant_id = :hospital_id
                      AND operation = 'treatment.write_vitals.v1'
                      AND idempotency_key = :idempotency_key
                    """
                ),
                {
                    "hospital_id": str(hospital_id),
                    "idempotency_key": idempotency_key,
                },
            )
            await db.execute(
                text("DELETE FROM timeline_events WHERE patient_id = :patient_id"),
                {"patient_id": patient_id},
            )
            await db.execute(
                text("DELETE FROM patient_vitals WHERE patient_id = :patient_id"),
                {"patient_id": patient_id},
            )
            await db.execute(
                text(
                    "DELETE FROM clinical_encounters "
                    "WHERE encounter_id = :encounter_id"
                ),
                {"encounter_id": encounter_id},
            )
            await db.execute(
                text(
                    "DELETE FROM clinical_access_sessions "
                    "WHERE session_id = :session_id"
                ),
                {"session_id": session_id},
            )
            await db.execute(
                text("DELETE FROM provider_identity WHERE id = :provider_id"),
                {"provider_id": provider_id},
            )
            await db.execute(
                text("DELETE FROM hospital_registry WHERE id = :hospital_id"),
                {"hospital_id": hospital_id},
            )
            await db.execute(
                text("DELETE FROM patients WHERE patient_uuid = :patient_id"),
                {"patient_id": patient_id},
            )
            await db.commit()
        await engine.dispose()
