from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from app.services.policy_service import PolicyService


@pytest.mark.asyncio
async def test_set_policy_uses_atomic_upsert() -> None:
    db = AsyncMock()
    patient_id = uuid.uuid4()

    result = await PolicyService(db).set_policy(patient_id, "push_biometric")

    assert result == "push_biometric"
    db.execute.assert_awaited_once()
    stmt = db.execute.await_args.args[0]
    compiled = str(stmt.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT" in compiled
    assert "patient_uuid" in compiled
    db.commit.assert_awaited_once()
