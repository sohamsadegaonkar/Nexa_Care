from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

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
