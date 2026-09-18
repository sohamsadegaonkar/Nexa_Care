from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _database_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    if not url.startswith(("postgresql+asyncpg://", "postgresql://")):
        pytest.skip("TEST_DATABASE_URL does not identify PostgreSQL")
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_canonical_encounter_constraints_are_installed():
    engine = create_async_engine(_database_url())
    try:
        async with engine.connect() as connection:
            rows = await connection.execute(
                text(
                    """
                    SELECT conname, pg_get_constraintdef(oid)
                    FROM pg_constraint
                    WHERE conrelid = 'clinical_encounters'::regclass
                    """
                )
            )
            definitions = {name: definition for name, definition in rows}

        assert "clinical_encounters_pkey" in definitions
        assert "uq_clinical_encounter_session" in definitions
        for name in (
            "fk_clinical_encounter_session",
            "fk_clinical_encounter_patient",
            "fk_clinical_encounter_provider",
            "fk_clinical_encounter_hospital",
        ):
            assert name in definitions
            assert "ON DELETE RESTRICT" in definitions[name]
    finally:
        await engine.dispose()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_canonical_encounter_unique_session_rejects_concurrent_duplicate():
    engine = create_async_engine(_database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    patient_id = uuid.uuid4()
    hospital_id = uuid.uuid4()
    provider_id = uuid.uuid4()
    session_id = uuid.uuid4()
    reserved_encounter_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    try:
        async with factory() as db:
            await db.execute(
                text(
                    """
                    INSERT INTO patients
                        (patient_uuid, public_patient_id, consent_assurance_policy, is_deleted)
                    VALUES (:patient_id, :public_id, 'STANDARD', false)
                    """
                ),
                {"patient_id": patient_id, "public_id": f"NC-{uuid.uuid4().hex[:24].upper()}"},
            )
            await db.execute(
                text(
                    """
                    INSERT INTO hospital_registry
                        (id, facility_code, legal_name, display_name, country_code, is_active)
                    VALUES (:hospital_id, :facility_code, 'Qualification Hospital',
                            'Qualification Hospital', 'IN', true)
                    """
                ),
                {"hospital_id": hospital_id, "facility_code": f"QUAL-{uuid.uuid4().hex[:12]}"},
            )
            await db.execute(
                text(
                    """
                    INSERT INTO provider_identity
                        (id, provider_uid, hospital_id, role, status, is_active)
                    VALUES (:provider_id, :provider_uid, :hospital_id,
                            'provider', 'active', true)
                    """
                ),
                {
                    "provider_id": provider_id,
                    "provider_uid": f"qual-{uuid.uuid4().hex[:16]}",
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
                        policy_version, issued_at, expires_at, status, encounter_id
                    ) VALUES (
                        :session_id, :patient_id, :provider_id, :hospital_id,
                        :request_id, :token_hash, 'treatment', 'treatment',
                        CAST(:operations AS JSONB), :binding_hash,
                        'clinical-access-v1', :issued_at, :expires_at,
                        'ACTIVE', :encounter_id
                    )
                    """
                ),
                {
                    "session_id": session_id,
                    "patient_id": patient_id,
                    "provider_id": provider_id,
                    "hospital_id": hospital_id,
                    "request_id": str(uuid.uuid4()),
                    "token_hash": "a" * 64,
                    "operations": json.dumps(["CREATE_ENCOUNTER"]),
                    "binding_hash": "b" * 64,
                    "issued_at": now,
                    "expires_at": now + timedelta(minutes=10),
                    "encounter_id": str(reserved_encounter_id),
                },
            )
            await db.commit()

        async def _attempt(encounter_id: uuid.UUID) -> str:
            async with factory() as db:
                try:
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
                    return "committed"
                except IntegrityError:
                    await db.rollback()
                    return "integrity_rejected"

        results = await asyncio.gather(
            _attempt(reserved_encounter_id),
            _attempt(uuid.uuid4()),
        )
        assert sorted(results) == ["committed", "integrity_rejected"]

        async with factory() as db:
            count = await db.scalar(
                text(
                    "SELECT count(*) FROM clinical_encounters "
                    "WHERE clinical_session_id = :session_id"
                ),
                {"session_id": session_id},
            )
            assert count == 1
    finally:
        async with factory() as db:
            await db.execute(
                text("DELETE FROM clinical_encounters WHERE clinical_session_id = :s"),
                {"s": session_id},
            )
            await db.execute(
                text("DELETE FROM clinical_access_sessions WHERE session_id = :s"),
                {"s": session_id},
            )
            await db.execute(
                text("DELETE FROM provider_identity WHERE id = :p"),
                {"p": provider_id},
            )
            await db.execute(
                text("DELETE FROM hospital_registry WHERE id = :h"),
                {"h": hospital_id},
            )
            await db.execute(
                text("DELETE FROM patients WHERE patient_uuid = :p"),
                {"p": patient_id},
            )
            await db.commit()
        await engine.dispose()
