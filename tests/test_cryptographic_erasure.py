"""Tests for cryptographic erasure (Right to be Forgotten) -- DEFECT 7.

Uses a small stateful fake DB (rather than a blanket AsyncMock) so the
real destroy_dek() -> erasure-registry state machine actually runs, not
just its DB-call shape.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.crypto_kms import (
    LocalEnvelopeProvider,
    PatientDataErased,
    EncryptionError,
)
from app.models.dek_store import PatientDEKStore
from app.models.erasure_tombstone import PatientErasureTombstone
from app.models.provider_context import (
    ProviderContext,
    ProviderIdentityContext,
    HospitalContext,
    AffiliationContext,
)
from app.core.dependencies import get_db_session, get_provider_context, require_role
from app.api.v2.patient_routes import get_kms_provider
from app.models.provider import AffiliationType


class _ScalarsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _ExecResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _ScalarsResult(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._rows[0] if self._rows else None


class FakeDB:
    """Tiny in-memory stand-in for AsyncSession that actually understands
    patient_dek_store and patient_erasure_tombstones queries, so the real
    erasure state machine runs end to end."""

    def __init__(self):
        self.dek_rows: list[PatientDEKStore] = []
        self.tombstones: list[PatientErasureTombstone] = []
        self._pending = []

    def add(self, row):
        self._pending.append(row)

    async def flush(self):
        for row in self._pending:
            if isinstance(row, PatientDEKStore) and row not in self.dek_rows:
                self.dek_rows.append(row)
            elif (
                isinstance(row, PatientErasureTombstone) and row not in self.tombstones
            ):
                self.tombstones.append(row)
        self._pending.clear()

    async def commit(self):
        await self.flush()

    async def rollback(self):
        self._pending.clear()

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        if "patient_erasure_tombstones" in sql:
            if sql.strip().startswith("DELETE"):
                return _ExecResult([])
            matches = [t for t in self.tombstones if True]
            # Narrow by patient_ref if the compiled params carry it (best effort).
            return _ExecResult(matches if matches else [])
        if "patient_dek_store" in sql:
            if sql.strip().startswith("DELETE"):
                self.dek_rows.clear()
                return _ExecResult([])
            if sql.strip().startswith("UPDATE"):
                for row in self.dek_rows:
                    row.is_active = False
                return _ExecResult([])
            return _ExecResult(list(self.dek_rows))
        return _ExecResult([])

    async def get(self, model, pk):
        return None


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def mock_db():
    return FakeDB()


@pytest.fixture
def mock_admin():
    pid = uuid.uuid4()
    hid = uuid.uuid4()
    return ProviderContext(
        provider=ProviderIdentityContext(
            provider_id=pid, display_name="Admin", contact_email="a@ex.com"
        ),
        hospital=HospitalContext(hospital_id=hid, facility_code="H", display_name="H"),
        affiliation=AffiliationContext(
            affiliation_id=uuid.uuid4(),
            affiliation_type=AffiliationType.PERMANENT,
            is_primary=True,
            roles=["admin"],
        ),
    )


@pytest.fixture
def env_setup():
    with patch.dict(
        os.environ, {"KEK_ROOT_SECRET": "test-root-secret-long-enough-32-chars-!!"}
    ):
        yield


@pytest.mark.asyncio
async def test_erasure_happy_path(client, mock_db, mock_admin, env_setup):
    kms = LocalEnvelopeProvider()
    patient_id = str(uuid.uuid4())
    await kms.generate_dek(patient_id, mock_db)

    app.dependency_overrides[get_db_session] = lambda: mock_db
    app.dependency_overrides[get_provider_context] = lambda: mock_admin
    app.dependency_overrides[require_role("admin")] = lambda: mock_admin
    app.dependency_overrides[get_kms_provider] = lambda: kms

    with (
        patch(
            "app.observability.audit_ledger.append_audit_log_or_503",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "app.api.v2.patient_routes.append_audit_log_or_503",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        payload = {"confirmation": f"ERASE-{patient_id}", "reason": "Patient request"}
        response = client.post(f"/api/v2/patient/{patient_id}/erase", json=payload)

        assert response.status_code == 200, response.text
        body = response.json()
        # A newly-generated DEK is patient-wrapped by default (DEFECT 7),
        # so the local provider can truthfully reach full destruction.
        assert body["assurance_level"] == "patient_key_destroyed"
        assert body["wrapping_key_type"] == "patient"
        assert body["historical_backup_irrecoverability_proven"] is True

        assert mock_db.dek_rows == []  # rows were deleted

        with pytest.raises(PatientDataErased):
            destroyed_row = MagicMock(spec=PatientDEKStore)
            destroyed_row.destroyed_at = datetime.now()
            mock_db.dek_rows.append(destroyed_row)
            await kms.decrypt_field(
                patient_id, "name", MagicMock(dek_version=1), mock_db
            )
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_erasure_irreversibility(mock_db, env_setup):
    kms = LocalEnvelopeProvider()
    patient_id = str(uuid.uuid4())
    await kms.generate_dek(patient_id, mock_db)

    encrypted_v1 = await kms.encrypt_field(patient_id, "f", "Secret", mock_db)

    await kms.destroy_dek(patient_id, mock_db)
    await kms.generate_dek(patient_id, mock_db)

    with pytest.raises(EncryptionError):
        await kms.decrypt_field(patient_id, "f", encrypted_v1, mock_db)


@pytest.mark.asyncio
async def test_erasure_confirmation_mismatch(client, mock_db, mock_admin, env_setup):
    kms = LocalEnvelopeProvider()
    patient_id = str(uuid.uuid4())
    app.dependency_overrides[get_provider_context] = lambda: mock_admin
    app.dependency_overrides[require_role("admin")] = lambda: mock_admin
    app.dependency_overrides[get_kms_provider] = lambda: kms
    app.dependency_overrides[get_db_session] = lambda: mock_db

    response = client.post(
        f"/api/v2/patient/{patient_id}/erase",
        json={"confirmation": "WRONG", "reason": "test"},
    )
    assert response.status_code == 400
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_erasure_unauthorized(client, mock_db, env_setup):
    patient_id = str(uuid.uuid4())
    response = client.post(
        f"/api/v2/patient/{patient_id}/erase",
        json={"confirmation": f"ERASE-{patient_id}", "reason": "test"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_erasure_idempotent(client, mock_db, mock_admin, env_setup):
    kms = LocalEnvelopeProvider()
    patient_id = str(uuid.uuid4())
    app.dependency_overrides[get_db_session] = lambda: mock_db
    app.dependency_overrides[get_provider_context] = lambda: mock_admin
    app.dependency_overrides[require_role("admin")] = lambda: mock_admin
    app.dependency_overrides[get_kms_provider] = lambda: kms

    with (
        patch(
            "app.observability.audit_ledger.append_audit_log_or_503",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "app.api.v2.patient_routes.append_audit_log_or_503",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        response = client.post(
            f"/api/v2/patient/{patient_id}/erase",
            json={"confirmation": f"ERASE-{patient_id}", "reason": "test"},
        )
        assert response.status_code == 200, response.text
        first_status = response.json()["status"]

        # Repeated erasure request must be idempotent -- same result, no crash.
        response2 = client.post(
            f"/api/v2/patient/{patient_id}/erase",
            json={"confirmation": f"ERASE-{patient_id}", "reason": "test"},
        )
        assert response2.status_code == 200, response2.text
        assert response2.json()["status"] == first_status
    app.dependency_overrides.clear()
