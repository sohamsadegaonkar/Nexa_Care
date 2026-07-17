"""Tests for atomic consent-gated decryption."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.services.consent_engine import ConsentCapability
from app.services.consent_gated_crypto import EncryptionProvider, consent_gated_decrypt


@pytest.fixture
def mock_kms():
    m = AsyncMock(spec=EncryptionProvider)
    # The new method is decrypt_field, not decrypt
    m.decrypt_field.side_effect = lambda pid, name, enc, db: f"decrypted-{enc.ciphertext.decode() if isinstance(enc.ciphertext, bytes) else enc}"
    return m


@pytest.fixture
def mock_redis():
    return AsyncMock()


@pytest.fixture
def mock_db():
    db = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_consent_gated_decrypt_happy_path(mock_kms, mock_redis, mock_db):
    patient_id = "patient-123"
    provider_id = "doctor-456"
    token = "valid-token"

    capability = ConsentCapability(
        patient_id=patient_id,
        clinician_id=provider_id,
        purpose="treatment",
        scope=["pii.*", "clinical.diagnoses"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-02T00:00:00+00:00",
    )

    # Mock vault fetch
    # Data must be in format "base64(iv+ciphertext):version"
    # "bmFtZQ==" is base64 for "name", but it needs to be at least 13 bytes for deserializer
    # Let's use a dummy that passes: 12 bytes IV + 4 bytes "data" = 16 bytes.
    # 16 bytes base64 is "YWJjZGVmZ2hpamtsbW5vcA=="
    mock_val = "YWJjZGVmZ2hpamtsbW5vcA==:1"
    mock_vault_row = SimpleNamespace(patient_name=mock_val, phone=mock_val, aadhaar_abha_id=None)
    mock_vault_res = MagicMock()
    mock_vault_res.scalars().first.return_value = mock_vault_row
    
    # Mock clinical fetch
    mock_clinical_row = SimpleNamespace(diagnoses=mock_val, lab_results=None, prescriptions=None)
    mock_clinical_res = MagicMock()
    mock_clinical_res.scalars().first.return_value = mock_clinical_row

    def execute_side_effect(stmt):
        stmt_str = str(stmt)
        if "nexa_vault" in stmt_str:
            return mock_vault_res
        if "nexa_clinical" in stmt_str:
            return mock_clinical_res
        return MagicMock()

    mock_db.execute.side_effect = execute_side_effect

    with patch("app.services.consent_engine.validate", return_value=capability), \
         patch("app.services.consent_engine.consume", return_value=capability), \
         patch("app.services.consent_gated_crypto.append_audit_log_or_503", new_callable=AsyncMock) as mock_audit:

        result = await consent_gated_decrypt(
            patient_id=patient_id,
            consent_token=token,
            purpose="treatment",
            requested_scope="*",
            provider_id=provider_id,
            hospital_id="hospital-1",
            db=mock_db,
            redis=mock_redis,
            kms=mock_kms
        )

        assert "decrypted-" in result["pii"]["patient_name"]
        assert "decrypted-" in result["pii"]["phone"]
        assert "decrypted-" in result["clinical"]["diagnoses"]
        assert mock_kms.decrypt_field.called
        assert mock_audit.await_count == 2


@pytest.mark.asyncio
async def test_consent_gated_decrypt_invalid_token(mock_kms, mock_redis, mock_db):
    with patch("app.services.consent_engine.validate", return_value=None):
        with pytest.raises(HTTPException) as exc:
            await consent_gated_decrypt(
                patient_id="p",
                consent_token="bad",
                purpose="t",
                requested_scope="*",
                provider_id="d",
                hospital_id="hospital-1",
                db=mock_db,
                redis=mock_redis,
                kms=mock_kms
            )
        assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_consent_gated_decrypt_scope_mismatch(mock_kms, mock_redis, mock_db):
    capability = ConsentCapability(
        patient_id="p",
        clinician_id="d",
        purpose="t",
        scope=["clinical.*"],
        is_break_glass=False,
        reason_code=None,
        issued_at="..."
    )
    with patch("app.services.consent_engine.validate", return_value=capability):
        with pytest.raises(HTTPException) as exc:
            await consent_gated_decrypt(
                patient_id="p",
                consent_token="tok",
                purpose="t",
                requested_scope="pii.name",
                provider_id="d",
                hospital_id="hospital-1",
                db=mock_db,
                redis=mock_redis,
                kms=mock_kms
            )
        assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_consent_gated_decrypt_decrypt_failure_consumes_token(mock_kms, mock_redis, mock_db):
    patient_id = "p1"
    provider_id = "d1"
    token = "tok1"
    capability = ConsentCapability(
        patient_id=patient_id,
        clinician_id=provider_id,
        purpose="t",
        scope=["pii.*"],
        is_break_glass=False,
        reason_code=None,
        issued_at="..."
    )

    mock_kms.decrypt_field.side_effect = RuntimeError("Key deleted")

    # Valid B64 serialized field
    mock_val = "YWJjZGVmZ2hpamtsbW5vcA==:1"
    mock_vault_row = SimpleNamespace(patient_name=mock_val, phone=None, aadhaar_abha_id=None)
    mock_res = MagicMock()
    mock_res.scalars().first.return_value = mock_vault_row
    mock_db.execute.return_value = mock_res

    with patch("app.services.consent_engine.validate", return_value=capability), \
         patch("app.services.consent_engine.consume", new_callable=AsyncMock) as mock_consume, \
         patch("app.services.consent_gated_crypto.append_audit_log_or_503", new_callable=AsyncMock), \
         patch("app.services.consent_gated_crypto.append_audit_log", new_callable=AsyncMock) as mock_audit:

        with pytest.raises(HTTPException) as exc:
            await consent_gated_decrypt(
                patient_id=patient_id,
                consent_token=token,
                purpose="t",
                requested_scope="pii.*",
                provider_id=provider_id,
                hospital_id="hospital-1",
                db=mock_db,
                redis=mock_redis,
                kms=mock_kms
            )

        assert exc.value.status_code == 500
        # Consent must be consumed even if decrypt fails
        assert mock_consume.called
        # FAILED audit entry
        assert any(call.kwargs.get("event_type") == "CONSENT_GATED_DECRYPT_FAILED" for call in mock_audit.call_args_list)


@pytest.mark.asyncio
async def test_consent_gated_decrypt_audit_failure_aborts(mock_kms, mock_redis, mock_db):
    capability = ConsentCapability(
        patient_id="p",
        clinician_id="d",
        purpose="t",
        scope=["pii.*"],
        is_break_glass=False,
        reason_code=None,
        issued_at="..."
    )

    with patch("app.services.consent_engine.validate", return_value=capability), \
         patch("app.services.consent_gated_crypto.append_audit_log_or_503", side_effect=HTTPException(status_code=503)):

        with pytest.raises(HTTPException) as exc:
            await consent_gated_decrypt(
                patient_id="p",
                consent_token="tok",
                purpose="t",
                requested_scope="pii.*",
                provider_id="d",
                hospital_id="hospital-1",
                db=mock_db,
                redis=mock_redis,
                kms=mock_kms
            )
        assert exc.value.status_code == 503
        assert not mock_kms.decrypt_field.called


@pytest.mark.asyncio
async def test_consent_gated_decrypt_passes_hospital_binding(mock_kms, mock_redis, mock_db):
    capability = ConsentCapability(
        patient_id="patient-1",
        clinician_id="doctor-1",
        purpose="treatment",
        scope=["pii.*"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-02T00:00:00+00:00",
    )

    with patch("app.services.consent_engine.validate", new_callable=AsyncMock, return_value=capability) as mock_validate:
        with pytest.raises(HTTPException):
            await consent_gated_decrypt(
                patient_id="patient-1",
                consent_token="token-1",
                purpose="treatment",
                requested_scope="clinical.diagnoses",
                provider_id="doctor-1",
                hospital_id="123e4567-e89b-12d3-a456-426614174001",
                db=mock_db,
                redis=mock_redis,
                kms=mock_kms,
            )

    mock_validate.assert_awaited_once_with(
        token="token-1",
        patient_id="patient-1",
        clinician_id="doctor-1",
        purpose="treatment",
        hospital_id="123e4567-e89b-12d3-a456-426614174001",
        session_binding=None,
    )
