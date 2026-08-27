"""Unit and route tests for patient profile creation, update, reading, and security.

Verifies:
- Creation provisions DEK in caller's transaction, encrypts PII, writes profile row, enqueues audit
- Plaintext PII barrier: stored columns contain valid EncryptedField serialized ciphertext
- Read path is strictly read-only: NEVER calls ensure_active_dek() or generate_dek()
- Read path with missing DEK fails closed with safe mapped error, zero DEK rows created
- Update modifies encrypted values, enqueues PATIENT_PROFILE_UPDATED with fields_changed metadata
- Exact retry is no-op: no ciphertext rewrite, no new audit event
- Validation: empty name, >200 chars, control characters, future DOB rejected
- Crypto error mapping: 410 for erased, 503 for unavailable; no secrets/PII in response bodies
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.config import ConfigError
from app.core.dependencies import AuthenticatedPatient, get_current_patient
from app.models.patient import Patient
from app.models.patient_profile import PatientProfile
from app.services.crypto_kms import (
    EncryptedField,
    EncryptionError,
    PatientDataErased,
    LocalEnvelopeProvider,
)
from app.services.patient_profile_service import (
    ProfileValidationError,
    create_or_update_profile,
    get_profile,
)


@pytest.fixture(autouse=True)
def _set_test_env(monkeypatch):
    monkeypatch.setenv(
        "KEK_ROOT_SECRET", "test-kek-root-secret-for-encryption-unit-tests!!"
    )
    monkeypatch.setenv(
        "PATIENT_JWT_SECRET", "test-secret-at-least-32-chars-long-here!!"
    )


# ---------------------------------------------------------------------------
# Service Unit Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_profile_validates_inputs():
    db = AsyncMock()
    pid = str(uuid.uuid4())

    # Empty name
    with pytest.raises(ProfileValidationError) as exc:
        await create_or_update_profile(pid, "   ", date(1990, 1, 1), db)
    assert exc.value.code == "INVALID_FULL_NAME"

    # Name > 200 chars
    with pytest.raises(ProfileValidationError) as exc:
        await create_or_update_profile(pid, "A" * 201, date(1990, 1, 1), db)
    assert exc.value.code == "INVALID_FULL_NAME"

    # Control chars in name
    with pytest.raises(ProfileValidationError) as exc:
        await create_or_update_profile(pid, "Test\x00Name", date(1990, 1, 1), db)
    assert exc.value.code == "INVALID_FULL_NAME"

    # Future DOB
    with pytest.raises(ProfileValidationError) as exc:
        await create_or_update_profile(
            pid, "Valid Name", date.today() + timedelta(days=1), db
        )
    assert exc.value.code == "INVALID_DATE_OF_BIRTH"


def _make_valid_serialized_field(field_name: str, dek_version: int = 1) -> str:
    field = EncryptedField(
        ciphertext=b"valid_ciphertext_123",
        iv=b"0123456789ab",
        field_name=field_name,
        dek_version=dek_version,
        algorithm="AES-256-GCM",
    )
    return field.serialize()


@pytest.mark.asyncio
async def test_create_profile_encrypts_and_enqueues_audit():
    pid = str(uuid.uuid4())
    patient_row = Patient(patient_uuid=uuid.UUID(pid), is_deleted=False)

    db = AsyncMock()
    added_objects = []
    db.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))

    # First select: Patient row (for update lock) -> returns patient_row
    # Second select: PatientProfile row -> returns None (create path)
    call_count = 0

    async def _execute(stmt):
        nonlocal call_count
        call_count += 1
        res = MagicMock()
        if call_count == 1:
            res.scalar_one_or_none.return_value = patient_row
        else:
            res.scalar_one_or_none.return_value = None
        return res

    db.execute = AsyncMock(side_effect=_execute)

    mock_kms = MagicMock(spec=LocalEnvelopeProvider)
    mock_kms.ensure_active_dek = AsyncMock()
    mock_kms.encrypt_field = AsyncMock(
        side_effect=lambda pid, f, val, db: EncryptedField(
            ciphertext=b"enc_" + val.encode(),
            iv=b"0123456789ab",
            field_name=f,
            dek_version=1,
            algorithm="AES-256-GCM",
        )
    )

    with (
        patch(
            "app.services.patient_profile_service.get_encryption_provider",
            return_value=mock_kms,
        ),
        patch(
            "app.services.patient_profile_service.enqueue_audit_event",
            new_callable=AsyncMock,
        ) as mock_audit,
    ):
        profile_data, created = await create_or_update_profile(
            pid, "Aarav Sharma", date(1990, 5, 15), db
        )

        assert created is True
        assert profile_data.full_name == "Aarav Sharma"
        assert profile_data.date_of_birth == "1990-05-15"

        # DEK was ensured
        mock_kms.ensure_active_dek.assert_awaited_once_with(pid, db)

        # Profile row added
        assert len(added_objects) == 1
        profile_obj = added_objects[0]
        assert isinstance(profile_obj, PatientProfile)
        assert profile_obj.patient_id == uuid.UUID(pid)
        assert "Aarav Sharma" not in profile_obj.full_name_encrypted
        assert "1990-05-15" not in profile_obj.date_of_birth_encrypted

        # Audit event was enqueued
        mock_audit.assert_awaited_once()
        audit_call = mock_audit.await_args.kwargs
        assert audit_call["event_type"] == "PATIENT_PROFILE_CREATED"
        assert audit_call["patient_id"] == pid


@pytest.mark.asyncio
async def test_update_profile_exact_retry_is_noop():
    pid = str(uuid.uuid4())
    patient_row = Patient(patient_uuid=uuid.UUID(pid), is_deleted=False)
    existing_profile = PatientProfile(
        patient_id=uuid.UUID(pid),
        full_name_encrypted=_make_valid_serialized_field("full_name"),
        date_of_birth_encrypted=_make_valid_serialized_field("date_of_birth"),
    )

    db = AsyncMock()
    call_count = 0

    async def _execute(stmt):
        nonlocal call_count
        call_count += 1
        res = MagicMock()
        if call_count == 1:
            res.scalar_one_or_none.return_value = patient_row
        else:
            res.scalar_one_or_none.return_value = existing_profile
        return res

    db.execute = AsyncMock(side_effect=_execute)

    mock_kms = MagicMock(spec=LocalEnvelopeProvider)
    mock_kms.decrypt_field = AsyncMock(
        side_effect=lambda pid, f, enc, db: "Aarav Sharma"
        if f == "full_name"
        else "1990-05-15"
    )
    mock_kms.encrypt_field = AsyncMock()

    with (
        patch(
            "app.services.patient_profile_service.get_encryption_provider",
            return_value=mock_kms,
        ),
        patch(
            "app.services.patient_profile_service.enqueue_audit_event",
            new_callable=AsyncMock,
        ) as mock_audit,
    ):
        profile_data, created = await create_or_update_profile(
            pid, "Aarav Sharma", date(1990, 5, 15), db
        )

        assert created is False
        assert profile_data.full_name == "Aarav Sharma"
        # No re-encryption, no audit event
        mock_kms.encrypt_field.assert_not_called()
        mock_audit.assert_not_called()


@pytest.mark.asyncio
async def test_update_profile_changes_values_and_audits():
    pid = str(uuid.uuid4())
    patient_row = Patient(patient_uuid=uuid.UUID(pid), is_deleted=False)
    existing_profile = PatientProfile(
        patient_id=uuid.UUID(pid),
        full_name_encrypted=_make_valid_serialized_field("full_name"),
        date_of_birth_encrypted=_make_valid_serialized_field("date_of_birth"),
    )

    db = AsyncMock()
    call_count = 0

    async def _execute(stmt):
        nonlocal call_count
        call_count += 1
        res = MagicMock()
        if call_count == 1:
            res.scalar_one_or_none.return_value = patient_row
        else:
            res.scalar_one_or_none.return_value = existing_profile
        return res

    db.execute = AsyncMock(side_effect=_execute)

    mock_kms = MagicMock(spec=LocalEnvelopeProvider)
    mock_kms.decrypt_field = AsyncMock(
        side_effect=lambda pid, f, enc, db: "Old Name"
        if f == "full_name"
        else "1990-05-15"
    )
    mock_kms.encrypt_field = AsyncMock(
        side_effect=lambda pid, f, val, db: EncryptedField(
            ciphertext=b"enc_" + val.encode(),
            iv=b"0123456789ab",
            field_name=f,
            dek_version=1,
            algorithm="AES-256-GCM",
        )
    )

    with (
        patch(
            "app.services.patient_profile_service.get_encryption_provider",
            return_value=mock_kms,
        ),
        patch(
            "app.services.patient_profile_service.enqueue_audit_event",
            new_callable=AsyncMock,
        ) as mock_audit,
    ):
        profile_data, created = await create_or_update_profile(
            pid, "New Name", date(1990, 5, 15), db
        )

        assert created is False
        assert profile_data.full_name == "New Name"

        # Encrypt called for new values
        assert mock_kms.encrypt_field.await_count == 2

        # Audit event with fields_changed
        mock_audit.assert_awaited_once()
        audit_call = mock_audit.await_args.kwargs
        assert audit_call["event_type"] == "PATIENT_PROFILE_UPDATED"
        assert audit_call["metadata"] == {"fields_changed": ["full_name"]}


@pytest.mark.asyncio
async def test_get_profile_is_strictly_read_only_and_does_not_provision_dek():
    pid = str(uuid.uuid4())
    mock_profile = PatientProfile(
        patient_id=uuid.UUID(pid),
        full_name_encrypted=_make_valid_serialized_field("full_name"),
        date_of_birth_encrypted=_make_valid_serialized_field("date_of_birth"),
    )

    db = AsyncMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = mock_profile
    db.execute = AsyncMock(return_value=res)

    mock_kms = MagicMock(spec=LocalEnvelopeProvider)
    mock_kms.decrypt_field = AsyncMock(
        side_effect=lambda pid, f, enc, db: "Aarav Sharma"
        if f == "full_name"
        else "1990-05-15"
    )
    mock_kms.ensure_active_dek = AsyncMock()
    mock_kms.generate_dek = AsyncMock()

    with patch(
        "app.services.patient_profile_service.get_encryption_provider",
        return_value=mock_kms,
    ):
        profile = await get_profile(pid, db)
        assert profile is not None
        assert profile.full_name == "Aarav Sharma"
        assert profile.date_of_birth == "1990-05-15"

        # CRITICAL INVARIANT: ensure_active_dek / generate_dek MUST NOT be called on read
        mock_kms.ensure_active_dek.assert_not_called()
        mock_kms.generate_dek.assert_not_called()


@pytest.mark.asyncio
async def test_get_profile_missing_dek_fails_closed_without_db_mutation():
    pid = str(uuid.uuid4())
    mock_profile = PatientProfile(
        patient_id=uuid.UUID(pid),
        full_name_encrypted=_make_valid_serialized_field("full_name"),
        date_of_birth_encrypted=_make_valid_serialized_field("date_of_birth"),
    )

    db = AsyncMock()
    db.add = MagicMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = mock_profile
    db.execute = AsyncMock(return_value=res)

    mock_kms = MagicMock(spec=LocalEnvelopeProvider)
    # Decrypt fails because DEK does not exist
    mock_kms.decrypt_field = AsyncMock(
        side_effect=EncryptionError(f"No active DEK found for patient {pid}")
    )
    mock_kms.ensure_active_dek = AsyncMock()

    with patch(
        "app.services.patient_profile_service.get_encryption_provider",
        return_value=mock_kms,
    ):
        with pytest.raises(EncryptionError):
            await get_profile(pid, db)

        # Zero DEKs provisioned, no db mutations
        mock_kms.ensure_active_dek.assert_not_called()
        db.add.assert_not_called()


# ---------------------------------------------------------------------------
# Route-Level Tests & Safe Error Mapping
# ---------------------------------------------------------------------------


def test_get_profile_404_when_no_profile():
    client = TestClient(app)
    test_pid = str(uuid.uuid4())

    async def mock_auth():
        return AuthenticatedPatient(
            patient_id=test_pid,
            patient=Patient(patient_uuid=uuid.UUID(test_pid)),
        )

    app.dependency_overrides[get_current_patient] = mock_auth

    with patch(
        "app.api.v2.patient_self_routes.get_profile",
        new_callable=AsyncMock,
        return_value=None,
    ):
        resp = client.get("/api/v2/patient/me/profile")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "PROFILE_NOT_FOUND"

    app.dependency_overrides.clear()


def test_get_profile_crypto_erased_returns_410_no_secrets():
    client = TestClient(app)
    test_pid = str(uuid.uuid4())

    async def mock_auth():
        return AuthenticatedPatient(
            patient_id=test_pid,
            patient=Patient(patient_uuid=uuid.UUID(test_pid)),
        )

    app.dependency_overrides[get_current_patient] = mock_auth

    with patch(
        "app.api.v2.patient_self_routes.get_profile",
        new_callable=AsyncMock,
        side_effect=PatientDataErased(test_pid),
    ):
        resp = client.get("/api/v2/patient/me/profile")
        assert resp.status_code == 410
        body = resp.json()
        assert body["detail"] == "PATIENT_DATA_ERASED"
        # Verify no secret, patient ID, ciphertext or stack trace in body
        assert test_pid not in str(body)

    app.dependency_overrides.clear()


def test_get_profile_config_error_returns_safe_503_without_dek_provisioning():
    client = TestClient(app)
    test_pid = str(uuid.uuid4())

    async def mock_auth():
        return AuthenticatedPatient(
            patient_id=test_pid,
            patient=Patient(patient_uuid=uuid.UUID(test_pid)),
        )

    app.dependency_overrides[get_current_patient] = mock_auth
    with patch(
        "app.api.v2.patient_self_routes.get_profile",
        new_callable=AsyncMock,
        side_effect=ConfigError("KMS configuration contains internal key material"),
    ) as mock_get_profile:
        resp = client.get("/api/v2/patient/me/profile")
        assert resp.status_code == 503
        assert resp.json()["detail"] == "ENCRYPTION_SERVICE_UNAVAILABLE"
        assert "key material" not in str(resp.json())
        assert test_pid not in str(resp.json())
        mock_get_profile.assert_awaited_once()

    app.dependency_overrides.clear()


def test_get_profile_provider_initialization_error_returns_safe_503():
    client = TestClient(app)
    test_pid = str(uuid.uuid4())
    provider_detail = "SENTINEL_PROVIDER_INITIALIZATION_DETAIL"

    async def mock_auth():
        return AuthenticatedPatient(
            patient_id=test_pid,
            patient=Patient(patient_uuid=uuid.UUID(test_pid)),
        )

    app.dependency_overrides[get_current_patient] = mock_auth
    with patch(
        "app.api.v2.patient_self_routes.get_profile",
        new_callable=AsyncMock,
        side_effect=EncryptionError(provider_detail),
    ) as mock_get_profile:
        resp = client.get("/api/v2/patient/me/profile")
        assert resp.status_code == 503
        assert resp.json()["detail"] == "ENCRYPTION_SERVICE_UNAVAILABLE"
        assert provider_detail not in str(resp.json())
        assert test_pid not in str(resp.json())
        mock_get_profile.assert_awaited_once()

    app.dependency_overrides.clear()


def test_profile_mutation_rejects_client_identity_and_server_owned_fields():
    client = TestClient(app)
    test_pid = str(uuid.uuid4())

    async def mock_auth():
        return AuthenticatedPatient(
            patient_id=test_pid,
            patient=Patient(patient_uuid=uuid.UUID(test_pid)),
        )

    app.dependency_overrides[get_current_patient] = mock_auth
    with patch(
        "app.api.v2.patient_self_routes.create_or_update_profile",
        new_callable=AsyncMock,
    ) as service:
        response = client.put(
            "/api/v2/patient/me/profile",
            json={
                "full_name": "Aarav Sharma",
                "date_of_birth": "1990-05-15",
                "patient_id": str(uuid.uuid4()),
                "supabase_user_id": "attacker-subject",
                "accepted_at": "2026-08-26T00:00:00Z",
                "timestamp": "2026-08-26T00:00:00Z",
            },
        )
        assert response.status_code == 422
        service.assert_not_awaited()

    app.dependency_overrides.clear()


def test_put_profile_crypto_error_returns_503_no_secrets():
    client = TestClient(app)
    test_pid = str(uuid.uuid4())

    async def mock_auth():
        return AuthenticatedPatient(
            patient_id=test_pid,
            patient=Patient(patient_uuid=uuid.UUID(test_pid)),
        )

    app.dependency_overrides[get_current_patient] = mock_auth

    with patch(
        "app.api.v2.patient_self_routes.create_or_update_profile",
        new_callable=AsyncMock,
        side_effect=EncryptionError(
            "AWS KMS Decrypt returned AccessDeniedException on key arn:aws:kms:..."
        ),
    ):
        resp = client.put(
            "/api/v2/patient/me/profile",
            json={"full_name": "Aarav Sharma", "date_of_birth": "1990-05-15"},
        )
        assert resp.status_code == 503
        body = resp.json()
        assert body["detail"] == "ENCRYPTION_SERVICE_UNAVAILABLE"
        # CRITICAL HYGIENE: no AWS error, ARN, patient_id or stack trace exposed
        assert "AccessDeniedException" not in str(body)
        assert "arn:aws:kms" not in str(body)
        assert test_pid not in str(body)

    app.dependency_overrides.clear()
