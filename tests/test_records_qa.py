"""Canonical vault and empty-record behavior."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.v2.patient_record_routes import _fetch_and_merge_timeline, _read_vault_identity


@pytest.mark.asyncio
async def test_missing_vault_row_returns_unknown_identity():
    db = AsyncMock()
    result = MagicMock(); result.scalar_one_or_none.return_value = None
    db.execute.return_value = result
    identity = await _read_vault_identity(uuid.uuid4(), db)
    assert identity == {"patient_name": None, "phone": None, "aadhaar_abha_id": None}


@pytest.mark.asyncio
async def test_plaintext_or_corrupt_vault_identity_fails_closed():
    db = AsyncMock()
    row = MagicMock(patient_name="plaintext", phone=None, aadhaar_abha_id=None)
    result = MagicMock(); result.scalar_one_or_none.return_value = row
    db.execute.return_value = result
    with pytest.raises(HTTPException) as exc:
        await _read_vault_identity(uuid.uuid4(), db)
    assert exc.value.status_code == 503
    assert exc.value.detail["error_code"] == "IDENTITY_UNAVAILABLE"


@pytest.mark.asyncio
async def test_empty_timeline_returns_empty_list_not_sample_events():
    db = AsyncMock()
    result = MagicMock(); result.scalars.return_value.all.return_value = []
    db.execute.return_value = result
    assert await _fetch_and_merge_timeline(str(uuid.uuid4()), db) == []
    assert db.execute.await_count == 5


@pytest.mark.asyncio
async def test_timeline_rejects_non_uuid_patient():
    with pytest.raises(HTTPException) as exc:
        await _fetch_and_merge_timeline("patient-1", AsyncMock())
    assert exc.value.status_code == 422
