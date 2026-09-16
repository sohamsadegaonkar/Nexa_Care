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
async def test_treatment_session_operation_constraints_are_installed():
    engine = create_async_engine(_database_url())
    try:
        async with engine.connect() as connection:
            rows = await connection.execute(
                text(
                    """
                    SELECT conname, pg_get_constraintdef(oid)
                    FROM pg_constraint
                    WHERE conrelid = 'clinical_access_sessions'::regclass
                      AND conname IN (
                        'ck_clinical_access_session_scope',
                        'ck_clinical_access_session_ops_v1'
                      )
                    """
                )
            )
            definitions = {name: definition for name, definition in rows}

        scope = " ".join(definitions["ck_clinical_access_session_scope"].split())
        operations = " ".join(
            definitions["ck_clinical_access_session_ops_v1"].split()
        )
        assert "treatment" in scope
        assert "READ_CLINICAL_HISTORY" in operations
        assert "CREATE_ENCOUNTER" in operations
        assert "WRITE_PRESCRIPTION" in operations
        assert "ORDER_INVESTIGATION" in operations
        assert "jsonb_array_length" in operations
        assert "<@" in operations
    finally:
        await engine.dispose()
