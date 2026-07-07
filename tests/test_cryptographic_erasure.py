"""Tests for cryptographic erasure (Right to be Forgotten)."""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.services.crypto_kms import LocalEnvelopeProvider, PatientDataErased, EncryptionError
from app.models.provider_context import ProviderContext, ProviderIdentityContext, HospitalContext, AffiliationContext
from app.core.dependencies import get_db_session, get_provider_context, require_role
from app.api.v2.patient_routes import get_kms_provider
from app.models.provider import AffiliationType

@pytest.fixture
def client():
    return TestClient(app)

@pytest.fixture
def mock_db():
    db = AsyncMock(spec=AsyncSession)
    res = MagicMock()
    db.execute.return_value = res
    return db

@pytest.fixture
def mock_admin():
    pid = uuid.uuid4()
    hid = uuid.uuid4()
    return ProviderContext(
        provider=ProviderIdentityContext(provider_id=pid, display_name="Admin", contact_email="a@ex.com"),
        hospital=HospitalContext(hospital_id=hid, facility_code="H", display_name="H"),
        affiliation=AffiliationContext(
            affiliation_id=uuid.uuid4(),
            affiliation_type=AffiliationType.PERMANENT,
            is_primary=True,
            roles=["admin"]
        )
    )

@pytest.fixture
def env_setup():
    with patch.dict(os.environ, {"KEK_ROOT_SECRET": "test-root-secret-long-enough-32-chars-!!"}):
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

    with patch("app.observability.audit_ledger.append_audit_log_or_503", new_callable=AsyncMock, return_value=None):
        payload = {"confirmation": f"ERASE-{patient_id}", "reason": "Patient request"}
        response = client.post(f"/api/v2/patient/{patient_id}/erase", json=payload)
        
        assert response.status_code == 200
        assert response.json()["status"] == "erased"
        
        from app.models.dek_store import PatientDEKStore
        destroyed_row = MagicMock(spec=PatientDEKStore)
        destroyed_row.destroyed_at = datetime.now()
        mock_db.execute.return_value.scalar_one_or_none.return_value = destroyed_row
        
        with pytest.raises(PatientDataErased):
             await kms.decrypt_field(patient_id, "name", MagicMock(dek_version=1), mock_db)
    app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_erasure_irreversibility(mock_db, env_setup):
    kms = LocalEnvelopeProvider()
    patient_id = str(uuid.uuid4())
    await kms.generate_dek(patient_id, mock_db)
    
    mock_db.scalar = AsyncMock(return_value=1)
    mock_row = MagicMock()
    mock_row.dek_version = 1
    mock_row.destroyed_at = None
    mock_db.execute.return_value.scalar_one_or_none.return_value = mock_row
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

    response = client.post(f"/api/v2/patient/{patient_id}/erase", json={"confirmation": "WRONG", "reason": "test"})
    assert response.status_code == 400
    app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_erasure_unauthorized(client, mock_db, env_setup):
    patient_id = str(uuid.uuid4())
    response = client.post(f"/api/v2/patient/{patient_id}/erase", json={"confirmation": f"ERASE-{patient_id}", "reason": "test"})
    assert response.status_code == 401

@pytest.mark.asyncio
async def test_erasure_idempotent(client, mock_db, mock_admin, env_setup):
    kms = LocalEnvelopeProvider()
    patient_id = str(uuid.uuid4())
    app.dependency_overrides[get_db_session] = lambda: mock_db
    app.dependency_overrides[get_provider_context] = lambda: mock_admin
    app.dependency_overrides[require_role("admin")] = lambda: mock_admin
    app.dependency_overrides[get_kms_provider] = lambda: kms
    
    res = MagicMock()
    res.scalars().all.return_value = []
    mock_db.execute.return_value = res
    
    with patch("app.observability.audit_ledger.append_audit_log_or_503", new_callable=AsyncMock, return_value=None):
        response = client.post(f"/api/v2/patient/{patient_id}/erase", json={"confirmation": f"ERASE-{patient_id}", "reason": "test"})
        assert response.status_code == 200
    app.dependency_overrides.clear()
