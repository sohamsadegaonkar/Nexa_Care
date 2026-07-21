from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import UniqueConstraint

from app.models.patient_auth_identity import PatientAuthIdentity
from scripts.link_patient_auth_identity import (
    PatientNotEligible,
    ProvisioningConflict,
    link_patient_auth_identity,
)


def test_model_prohibits_duplicate_provider_subject_and_indexes_patient() -> None:
    table = PatientAuthIdentity.__table__
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("provider", "provider_subject") in unique_columns
    assert "ix_patient_auth_identities_patient_id" in {
        index.name for index in table.indexes
    }
    foreign_key = next(iter(table.c.patient_id.foreign_keys))
    assert foreign_key.target_fullname == "patients.patient_uuid"
    assert foreign_key.ondelete == "RESTRICT"


@pytest.mark.asyncio
async def test_cli_provisions_mapping_and_audits() -> None:
    patient_id = uuid4()
    session = AsyncMock()
    session.add = MagicMock()
    session.scalar.side_effect = [
        SimpleNamespace(patient_uuid=patient_id, is_deleted=False),
        None,
    ]
    with patch(
        "scripts.link_patient_auth_identity.append_audit_log",
        new=AsyncMock(return_value=True),
    ) as audit:
        status = await link_patient_auth_identity(
            session,
            patient_id=patient_id,
            supabase_user_id="supabase-user-1",
        )
    assert status == "linked"
    mapping = session.add.call_args.args[0]
    assert mapping.patient_id == patient_id
    assert mapping.provider == "supabase"
    assert mapping.provider_subject == "supabase-user-1"
    session.flush.assert_awaited_once()
    session.commit.assert_awaited_once()
    assert audit.await_args.kwargs["event_type"] == "PATIENT_AUTH_IDENTITY_LINKED"


@pytest.mark.asyncio
async def test_cli_is_idempotent_for_same_active_mapping() -> None:
    patient_id = uuid4()
    existing = SimpleNamespace(patient_id=patient_id, revoked_at=None)
    session = AsyncMock()
    session.scalar.side_effect = [SimpleNamespace(patient_uuid=patient_id), existing]
    status = await link_patient_auth_identity(
        session,
        patient_id=patient_id,
        supabase_user_id="supabase-user-1",
    )
    assert status == "already-linked"
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_cli_rejects_conflicting_patient_mapping() -> None:
    requested_patient = uuid4()
    session = AsyncMock()
    session.scalar.side_effect = [
        SimpleNamespace(patient_uuid=requested_patient),
        SimpleNamespace(patient_id=uuid4(), revoked_at=None),
    ]
    with pytest.raises(ProvisioningConflict):
        await link_patient_auth_identity(
            session,
            patient_id=requested_patient,
            supabase_user_id="supabase-user-1",
        )


@pytest.mark.asyncio
async def test_cli_rejects_missing_or_deleted_patient() -> None:
    session = AsyncMock()
    session.scalar.return_value = None
    with pytest.raises(PatientNotEligible):
        await link_patient_auth_identity(
            session,
            patient_id=UUID("123e4567-e89b-12d3-a456-426614174001"),
            supabase_user_id="supabase-user-1",
        )


@pytest.mark.asyncio
async def test_cli_rolls_back_mapping_when_audit_write_fails() -> None:
    patient_id = uuid4()
    session = AsyncMock()
    session.add = MagicMock()
    session.scalar.side_effect = [
        SimpleNamespace(patient_uuid=patient_id, is_deleted=False),
        None,
    ]

    with patch(
        "scripts.link_patient_auth_identity.append_audit_log",
        new=AsyncMock(return_value=False),
    ):
        with pytest.raises(RuntimeError, match="Audit ledger write failed"):
            await link_patient_auth_identity(
                session,
                patient_id=patient_id,
                supabase_user_id="supabase-user-rollback",
            )

    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
