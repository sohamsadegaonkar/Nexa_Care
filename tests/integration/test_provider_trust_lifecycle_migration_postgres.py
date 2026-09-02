"""Disposable PostgreSQL qualification for Phase-3C lifecycle generations."""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

PREVIOUS_HEAD = "20260902_contact_assurance"
CURRENT_HEAD = "20260903_trust_lifecycle"


def _url(name: str) -> str:
    value = os.getenv(name, "")
    if not value:
        pytest.skip(f"{name} is not configured")
    normalized = value.replace("postgresql://", "postgresql+asyncpg://", 1)
    if "127.0.0.1" not in normalized and "localhost" not in normalized:
        pytest.fail(f"{name} must be loopback-only")
    if "nexa_qual_" not in normalized:
        pytest.fail(f"{name} must name a disposable nexa_qual_ database")
    return normalized


def _config(url: str) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    return config


async def _seed_previous_head(url: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Insert real pre-version rows using only columns available at the old head."""
    provider_id, facility_id, affiliation_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            await db.execute(text("""
                INSERT INTO hospital_registry
                    (id, facility_code, legal_name, display_name, country_code, is_active, created_at, updated_at)
                VALUES (:id, :code, 'Synthetic Lifecycle Facility', 'Synthetic Lifecycle Facility', 'IN', TRUE, now(), now())
            """), {"id": facility_id, "code": f"LIFE-{facility_id.hex[:16]}"})
            await db.execute(text("""
                INSERT INTO provider_identity
                    (id, provider_uid, hospital_id, role, status, is_active, created_at, updated_at)
                VALUES (:id, :uid, :hospital_id, 'provider', 'active', TRUE, now(), now())
            """), {"id": provider_id, "uid": str(provider_id), "hospital_id": facility_id})
            await db.execute(text("""
                INSERT INTO professional_verification
                    (id, provider_id, status, previous_verification_valid, created_at, updated_at)
                VALUES (gen_random_uuid(), :provider_id, 'NOT_SUBMITTED', FALSE, now(), now())
            """), {"provider_id": provider_id})
            await db.execute(text("""
                INSERT INTO facility_verification
                    (id, facility_id, status, created_at, updated_at)
                VALUES (gen_random_uuid(), :facility_id, 'DRAFT', now(), now())
            """), {"facility_id": facility_id})
            await db.execute(text("""
                INSERT INTO provider_hospital_affiliation
                    (id, provider_id, hospital_id, affiliation_type, roles, is_primary, is_active, trust_status, created_at, updated_at)
                VALUES (:id, :provider_id, :hospital_id, 'permanent', '[]'::jsonb, FALSE, TRUE, 'PENDING_ACTIVATION', now(), now())
            """), {"id": affiliation_id, "provider_id": provider_id, "hospital_id": facility_id})
            await db.commit()
    finally:
        await engine.dispose()
    return provider_id, facility_id, affiliation_id


@pytest.mark.asyncio
async def test_fresh_postgres_upgrade_creates_positive_lifecycle_versions(
    monkeypatch,
) -> None:
    """A fresh disposable database upgrades directly to the sole current head."""
    url = _url("TRUST_LIFECYCLE_FRESH_DATABASE_URL")
    # The repository's asynchronous Alembic environment intentionally reads
    # this value rather than Config.sqlalchemy.url.
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    await asyncio.to_thread(command.upgrade, _config(url), CURRENT_HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            version = await db.scalar(text("SELECT version_num FROM alembic_version"))
            assert version == CURRENT_HEAD
            for table in ("professional_verification", "facility_verification", "provider_hospital_affiliation"):
                default = await db.scalar(text("""
                    SELECT column_default FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = :table AND column_name = 'version'
                """), {"table": table})
                assert default is not None and "1" in str(default)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_previous_head_upgrade_backfills_versions_and_enforces_positive_values(
    monkeypatch,
) -> None:
    """Existing trust rows become version 1; PostgreSQL rejects zero versions."""
    url = _url("TRUST_LIFECYCLE_PREVIOUS_DATABASE_URL")
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    config = _config(url)
    await asyncio.to_thread(command.upgrade, config, PREVIOUS_HEAD)
    provider_id, facility_id, affiliation_id = await _seed_previous_head(url)
    await asyncio.to_thread(command.upgrade, config, CURRENT_HEAD)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            assert await db.scalar(text("SELECT version FROM professional_verification WHERE provider_id = :id"), {"id": provider_id}) == 1
            assert await db.scalar(text("SELECT version FROM facility_verification WHERE facility_id = :id"), {"id": facility_id}) == 1
            assert await db.scalar(text("SELECT version FROM provider_hospital_affiliation WHERE id = :id"), {"id": affiliation_id}) == 1
            nulls_or_non_positive = await db.scalar(text("""
                SELECT
                    (SELECT count(*) FROM professional_verification WHERE version IS NULL OR version <= 0) +
                    (SELECT count(*) FROM facility_verification WHERE version IS NULL OR version <= 0) +
                    (SELECT count(*) FROM provider_hospital_affiliation WHERE version IS NULL OR version <= 0)
            """))
            assert nulls_or_non_positive == 0
        for statement, values in (
            (
                "UPDATE professional_verification SET version = 0 WHERE provider_id = :id",
                {"id": provider_id},
            ),
            (
                "UPDATE facility_verification SET version = 0 WHERE facility_id = :id",
                {"id": facility_id},
            ),
            (
                "UPDATE provider_hospital_affiliation SET version = 0 WHERE id = :id",
                {"id": affiliation_id},
            ),
        ):
            async with factory() as db:
                with pytest.raises(IntegrityError):
                    await db.execute(text(statement), values)
                    await db.commit()
                await db.rollback()
    finally:
        await engine.dispose()
