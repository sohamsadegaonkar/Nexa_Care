"""Integration tests for Squad C: Cryptographic Erasure.

Contract:
- Overwrite/delete DEK for specific patient.
- Verify decryption now fails with PatientDataErased.
- Audit trail for destruction.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.v2.patient_routes import _fetch_clinical_shard
from app.models.shards import NexaClinical
from app.services.crypto_kms import LocalEnvelopeProvider, PatientDataErased

@pytest.mark.integration
@pytest.mark.xfail(reason="Squad C Day 4: Cryptographic erasure logic not implemented")
@pytest.mark.asyncio
async def test_cryptographic_erasure_workflow(test_client):
    """Test: Destroy DEK -> Decrypt fails with PatientDataErased.
    
    Ensures that once the key is destroyed, the corresponding 
    ciphertext in nexa_vault becomes mathematically unreadable.
    """
    patient_id = "p-123"
    
    # 1. Erase
    erase_resp = await test_client.post(f"/api/v2/patient/{patient_id}/erase", json={
        "confirmation": f"ERASE-{patient_id}",
        "reason": "Request by patient"
    })
    assert erase_resp.status_code == 200
    
    # 2. Attempt read
    # Requires a valid consent token (setup omitted for skeleton)
    headers = {"X-Consent-Token": "valid-token", "X-Consent-Purpose": "TREATMENT"}
    read_resp = await test_client.get(f"/api/v2/patient/{patient_id}/record", headers=headers)
    
    # The record endpoint should return a 500 or 410 indicating the data is gone
    assert read_resp.status_code in [500, 410]
    assert "erased" in read_resp.text.lower()

@pytest.mark.integration
@pytest.mark.asyncio
async def test_clinical_data_persists_after_pii_erasure(monkeypatch):
    """Test: clinical shard rows remain readable after PII DEK destruction."""
    monkeypatch.setenv("KEK_ROOT_SECRET", "test-root-secret-long-enough-32-chars-!!")

    patient_id = str(uuid.uuid4())
    db = MagicMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.execute = AsyncMock()

    kms = LocalEnvelopeProvider()
    await kms.generate_dek(patient_id, db)
    dek_row = db.add.call_args.args[0]

    active_dek_result = MagicMock()
    active_dek_result.scalar_one_or_none.return_value = dek_row
    db.execute.return_value = active_dek_result
    encrypted_name = await kms.encrypt_field(patient_id, "patient_name", "Jane Doe", db)

    async def execute_for_destroy(stmt):
        result = MagicMock()
        if "patient_dek_store" in str(stmt):
            result.scalars().all.return_value = [dek_row]
        return result

    db.execute.side_effect = execute_for_destroy
    with patch("app.observability.audit_ledger.append_audit_log", AsyncMock()):
        await kms.destroy_dek(patient_id, db)

    clinical_row = NexaClinical(
        masked_internal_id=patient_id,
        diagnoses=["hypertension"],
        lab_results=["HbA1c 6.1%"],
        prescriptions=["metformin"],
        clinical_data={"blood_pressure": "120/80"},
    )

    async def execute_after_erasure(stmt):
        result = MagicMock()
        stmt_text = str(stmt)
        if "nexa_clinical" in stmt_text:
            result.scalars().first.return_value = clinical_row
        elif "patient_dek_store" in stmt_text:
            result.scalar_one_or_none.return_value = dek_row
        return result

    db.execute.side_effect = execute_after_erasure

    clinical_payload = await _fetch_clinical_shard(patient_id, db)
    assert clinical_payload["diagnoses"] == ["hypertension"]
    assert clinical_payload["lab_results"] == ["HbA1c 6.1%"]
    assert clinical_payload["prescriptions"] == ["metformin"]
    assert clinical_payload["blood_pressure"] == "120/80"

    with pytest.raises(PatientDataErased):
        await kms.decrypt_field(patient_id, "patient_name", encrypted_name, db)
