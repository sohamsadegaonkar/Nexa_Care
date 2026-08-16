"""Real-PostgreSQL checks for Milestone 3 routing schema constraints."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


def _database_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    if not url.startswith(("postgresql+asyncpg://", "postgresql://")):
        pytest.skip("TEST_DATABASE_URL does not identify PostgreSQL")
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_extraction_routing_tables_and_safe_lane_constraints_exist():
    engine = create_async_engine(_database_url())
    try:
        async with engine.connect() as connection:
            rows = await connection.execute(
                text(
                    """
                    SELECT conname
                    FROM pg_constraint
                    WHERE conrelid IN (
                        'extraction_decisions'::regclass,
                        'extraction_routing'::regclass
                    )
                    """
                )
            )
            constraints = set(rows.scalars())
        assert {
            "ck_extraction_decisions_safe_lane",
            "ck_extraction_decisions_auto_commit_disabled",
            "ck_extraction_decisions_organization_tenant",
            "ck_extraction_routing_safe_lane",
            "ck_extraction_routing_status",
            "ck_extraction_routing_lane_state",
            "ck_extraction_routing_escalation_time",
        } <= constraints
    finally:
        await engine.dispose()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_extraction_routing_database_uniqueness_guards_exist():
    engine = create_async_engine(_database_url())
    try:
        async with engine.connect() as connection:
            rows = await connection.execute(
                text(
                    """
                    SELECT conname
                    FROM pg_constraint
                    WHERE conrelid = 'extraction_routing'::regclass
                      AND contype = 'u'
                    """
                )
            )
            constraints = set(rows.scalars())
        assert {
            "uq_extraction_routing_decision",
            "uq_extraction_routing_idempotency",
        } <= constraints
    finally:
        await engine.dispose()
