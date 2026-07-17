from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from app.services.policy_service import PolicyService


@pytest.mark.asyncio
async def test_get_policy_awaits_mocked_policy_value() -> None:
    db = AsyncMock()
    patient_id = uuid.uuid4()
    policy = AsyncMock()
    policy.consent_assurance_policy = AsyncMock(return_value="push_approved")()
    db.get.return_value = policy

    result = await PolicyService(db).get_policy(patient_id)

    assert result == "push_approved"
    db.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_policy_defaults_when_policy_value_is_blank() -> None:
    db = AsyncMock()
    patient_id = uuid.uuid4()
    policy = AsyncMock()
    policy.consent_assurance_policy = " "
    db.get.return_value = policy

    result = await PolicyService(db).get_policy(patient_id)

    assert result == "standard"


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
