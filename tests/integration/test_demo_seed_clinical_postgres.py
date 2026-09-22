"""PostgreSQL proof that concurrent local-demo clinical seeding stays singular."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from scripts.seed_demo_doctor import seed_clinical_records
from tests.helpers.qualification_infra import (
    create_disposable_database,
    drop_disposable_database,
    migrate_database_to_head,
    postgres_database_url,
)

pytestmark = [pytest.mark.integration, pytest.mark.postgres, pytest.mark.asyncio]

_DB_NAME = "nexa_qual_demo_seed_clinical_lock"


@pytest.fixture(scope="module", autouse=True)
def disposable_database():
    """Create only the loopback, prefix-guarded database used by this module."""

    database_url = postgres_database_url(_DB_NAME)
    asyncio.run(create_disposable_database(_DB_NAME))
    migrate_database_to_head(database_url, target_head="20260919_medication_catalog")
    yield database_url
    asyncio.run(drop_disposable_database(_DB_NAME))


async def test_concurrent_first_run_seeding_creates_one_canonical_clinical_row(
    disposable_database: str,
):
    engine = create_async_engine(disposable_database)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    patient_id = uuid.uuid4()
    patient_id_text = str(patient_id)
    ready = asyncio.Barrier(2)

    async def seed_once() -> None:
        await ready.wait()
        async with session_factory() as session:
            await seed_clinical_records(
                session,
                patient_id,
                "aarav",
                patient_created=True,
            )
            await session.commit()

    try:
        await asyncio.gather(seed_once(), seed_once())
        async with session_factory() as session:
            count = await session.scalar(
                text(
                    "SELECT count(*) FROM nexa_clinical "
                    "WHERE masked_internal_id = CAST(:patient_id AS VARCHAR(64))"
                ),
                {"patient_id": patient_id_text},
            )
        assert count == 1
    finally:
        await engine.dispose()
