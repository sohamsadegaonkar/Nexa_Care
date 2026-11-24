import asyncio
import os
import sys
from urllib.parse import urlsplit

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from scripts.run_pilot_migrations import EXPECTED_HEAD
from tests.helpers.qualification_infra import (
    require_disposable_database_name,
    require_loopback_postgres_url,
)


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_azure_alembic_refuses_a_second_session_then_recovers():
    url = os.environ["TEST_DATABASE_URL"]
    require_loopback_postgres_url(url)
    require_disposable_database_name(urlsplit(url).path.lstrip("/"))
    environment = {**os.environ, "NEXA_AZURE_STAGING_MIGRATION_LOCK": "1"}

    async def current():
        command = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "alembic",
            "current",
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            output, errors = await asyncio.wait_for(command.communicate(), timeout=20)
        except TimeoutError:
            command.kill()
            await command.wait()
            raise
        return command.returncode, output, errors

    engine = create_async_engine(url)
    try:
        async with engine.connect() as owner:
            await owner.execute(text("SELECT pg_advisory_lock(20261124, 1)"))
            try:
                code, _, errors = await current()
                assert code != 0
                assert b"AZURE_STAGING_MIGRATION_ALREADY_RUNNING" in errors
            finally:
                await owner.execute(text("SELECT pg_advisory_unlock(20261124, 1)"))
        code, output, _ = await current()
        assert code == 0
        assert EXPECTED_HEAD.encode() in output
    finally:
        await engine.dispose()
