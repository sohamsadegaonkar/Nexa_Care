"""Real-PostgreSQL checks for Milestone 4.1 adjudication integrity."""

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
async def test_adjudication_hardening_constraints_exist_on_postgres():
    engine = create_async_engine(_database_url())
    try:
        async with engine.connect() as connection:
            rows = await connection.execute(
                text(
                    """
                    SELECT conname
                    FROM pg_constraint
                    WHERE conrelid IN (
                        'adjudication_cases'::regclass,
                        'adjudication_submissions'::regclass
                    )
                    """
                )
            )
            constraints = set(rows.scalars())
        assert {
            "ck_adjudication_cases_version_positive",
            "ck_adjudication_cases_operation_hash_length",
            "ck_adjudication_submissions_attempt_positive",
            "ck_adjudication_submissions_content_hash_length",
            "ck_adjudication_submissions_source_binding",
            "fk_adjudication_cases_accepted_submission_same_case",
            "fk_adjudication_submissions_document",
            "fk_adjudication_submissions_job",
            "fk_adjudication_submissions_routing",
            "fk_adjudication_submissions_decision",
            "uq_adjudication_submissions_case_id_id",
        } <= constraints
    finally:
        await engine.dispose()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_same_case_accepted_submission_uses_composite_postgres_fk():
    engine = create_async_engine(_database_url())
    try:
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        """
                        SELECT pg_get_constraintdef(oid)
                        FROM pg_constraint
                        WHERE conname =
                            'fk_adjudication_cases_accepted_submission_same_case'
                        """
                    )
                )
            ).scalar_one()
        normalized = " ".join(row.split())
        assert "FOREIGN KEY (id, accepted_submission_id)" in normalized
        assert "REFERENCES adjudication_submissions(case_id, id)" in normalized
    finally:
        await engine.dispose()
