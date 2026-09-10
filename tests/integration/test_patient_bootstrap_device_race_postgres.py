"""Real PostgreSQL qualification for first-device bootstrap serialization."""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.patient_device_keys import PatientDeviceKey
from app.services.patient_device_trust import (
    PatientDeviceTrustError,
    enroll_bootstrap_patient_device_key,
)
from tests.helpers.qualification_infra import (
    create_disposable_database,
    drop_disposable_database,
    migrate_database_to_head,
    postgres_database_url,
)

pytestmark = [pytest.mark.integration, pytest.mark.postgres, pytest.mark.asyncio]

HEAD = "20260909_device_trust_lifecycle"
_DB_NAME = "nexa_qual_patient_bootstrap_device_race"


def _url() -> str:
    return postgres_database_url(_DB_NAME)


@pytest.fixture(scope="module", autouse=True)
def _setup_database():
    previous = os.environ.get("TEST_DATABASE_URL")
    db_url = _url()
    os.environ["TEST_DATABASE_URL"] = db_url
    asyncio.run(create_disposable_database(_DB_NAME))
    migrate_database_to_head(db_url, target_head=HEAD)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("TEST_DATABASE_URL", None)
        else:
            os.environ["TEST_DATABASE_URL"] = previous
        asyncio.run(drop_disposable_database(_DB_NAME))


@pytest.fixture
async def session_factory():
    engine = create_async_engine(_url(), pool_size=8, max_overflow=0)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def _key() -> bytes:
    private_key = ec.generate_private_key(ec.SECP256R1())
    return private_key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


async def test_concurrent_bootstrap_enrollment_commits_exactly_one_device(session_factory):
    """Two valid bootstrap grants must not create two first trusted devices."""

    patient_id = uuid.uuid4()

    async def contender(raw_key: bytes) -> str:
        async with session_factory() as db:
            try:
                await enroll_bootstrap_patient_device_key(
                    db,
                    patient_id=patient_id,
                    raw_public_key=raw_key,
                    device_label="bootstrap-race-device",
                    platform="test",
                    actor_id=str(patient_id),
                )
                return "enrolled"
            except PatientDeviceTrustError as exc:
                return exc.code

    results = await asyncio.gather(contender(_key()), contender(_key()))

    assert results.count("enrolled") == 1
    assert results.count("DEVICE_RECOVERY_REQUIRED") == 1

    async with session_factory() as db:
        count = await db.scalar(
            select(func.count(PatientDeviceKey.id)).where(
                PatientDeviceKey.patient_id == patient_id
            )
        )
    assert int(count or 0) == 1
