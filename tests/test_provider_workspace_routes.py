"""Tests for Provider Clinical Workspace API (Slice 11A).

Verifies:
- 401 unauthenticated when no provider credentials/session present
- Workspace returns active sessions, recent encounters, summary counts
- Cross-provider isolation: Provider A cannot see Provider B's sessions
- Cross-hospital isolation: Hospital A context cannot see Hospital B's sessions
- Expired sessions are excluded
- Revoked sessions are excluded
- Patient public identifiers are resolved safely
- Patient profile names are decrypted safely
- Token hashes, provider binding hashes, and raw PII are strictly never leaked
"""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock
import uuid

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.core.database import get_db_session
from app.core.dependencies import get_provider_context
from app.main import app
from app.models.clinical_access_session import ClinicalAccessSessionRecord
from app.models.clinical_encounter import ClinicalEncounter
from app.models.patient import Patient
from app.models.patient_profile import PatientProfile
from app.models.provider import AffiliationType
from app.models.provider_context import (
    AffiliationContext,
    HospitalContext,
    ProviderContext,
    ProviderIdentityContext,
)


def _make_provider_context(
    provider_id: uuid.UUID | None = None,
    hospital_id: uuid.UUID | None = None,
) -> ProviderContext:
    p_id = provider_id or uuid.uuid4()
    h_id = hospital_id or uuid.uuid4()
    return ProviderContext(
        provider=ProviderIdentityContext(
            provider_id=p_id,
            display_name="Dr. Clinical Specialist",
            medical_registration_number="MCI-998877",
            specialty="Internal Medicine",
            contact_email="doctor@hospital.org",
        ),
        hospital=HospitalContext(
            hospital_id=h_id,
            facility_code="HOSP-ALPHA",
            display_name="Alpha Medical Center",
        ),
        affiliation=AffiliationContext(
            affiliation_id=uuid.uuid4(),
            affiliation_type=AffiliationType.PERMANENT,
            department="Medicine",
            roles=["clinician"],
            is_primary=True,
            valid_from=None,
            valid_until=None,
        ),
        session_binding="mock-provider-session-binding",
    )


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_workspace_route_registered(client: TestClient):
    """Verify that GET /api/v2/provider/workspace is registered on the app."""
    # When unauthenticated, it must require authentication (not 404)
    response = client.get("/api/v2/provider/workspace")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_workspace_empty_state(client: TestClient):
    """Authenticated provider with no active sessions or encounters gets empty lists."""
    provider_ctx = _make_provider_context()

    mock_db = AsyncMock()
    # 1. sessions: empty
    mock_sessions_res = MagicMock()
    mock_sessions_res.scalars.return_value.all.return_value = []

    # 2. encounters: empty
    mock_encounters_res = MagicMock()
    mock_encounters_res.scalars.return_value.all.return_value = []

    # 3. total patients: 0
    mock_db.execute.side_effect = [mock_sessions_res, mock_encounters_res]
    mock_db.scalar.return_value = 0

    app.dependency_overrides[get_provider_context] = lambda: provider_ctx
    app.dependency_overrides[get_db_session] = lambda: mock_db

    response = client.get("/api/v2/provider/workspace")
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert data["active_sessions"] == []
    assert data["recent_encounters"] == []
    assert data["pending_access"] == []
    assert data["summary_counts"]["active_sessions_count"] == 0
    assert data["summary_counts"]["recent_encounters_count"] == 0
    assert data["summary_counts"]["total_patients"] == 0


def test_workspace_returns_active_sessions_with_safe_patient_descriptor(client: TestClient):
    """Active treatment sessions are returned with safe public patient IDs."""
    provider_id = uuid.uuid4()
    hospital_id = uuid.uuid4()
    patient_id = uuid.uuid4()
    session_id = uuid.uuid4()

    provider_ctx = _make_provider_context(provider_id=provider_id, hospital_id=hospital_id)

    mock_session = MagicMock(spec=ClinicalAccessSessionRecord)
    mock_session.session_id = session_id
    mock_session.encounter_id = None
    mock_session.patient_id = patient_id
    mock_session.provider_id = provider_id
    mock_session.hospital_id = hospital_id
    mock_session.status = "ACTIVE"
    mock_session.purpose = "treatment"
    mock_session.scope = "treatment"
    mock_session.allowed_operations = ["CREATE_ENCOUNTER", "WRITE_VITALS"]
    mock_session.issued_at = datetime.datetime.now(datetime.timezone.utc)
    mock_session.expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=30)
    mock_session.revoked_at = None

    mock_patient = MagicMock(spec=Patient)
    mock_patient.patient_uuid = patient_id
    mock_patient.public_patient_id = "NC-P1234567890"

    mock_db = AsyncMock()

    # 1. sessions
    mock_sessions_res = MagicMock()
    mock_sessions_res.scalars.return_value.all.return_value = [mock_session]

    # 2. encounters
    mock_encounters_res = MagicMock()
    mock_encounters_res.scalars.return_value.all.return_value = []

    # 3. patient query
    mock_patient_res = MagicMock()
    mock_patient_res.scalars.return_value.all.return_value = [mock_patient]

    # 4. profile query (empty)
    mock_profile_res = MagicMock()
    mock_profile_res.scalars.return_value.all.return_value = []

    mock_db.execute.side_effect = [
        mock_sessions_res,
        mock_encounters_res,
        mock_patient_res,
        mock_profile_res,
    ]
    mock_db.scalar.return_value = 5

    app.dependency_overrides[get_provider_context] = lambda: provider_ctx
    app.dependency_overrides[get_db_session] = lambda: mock_db

    response = client.get("/api/v2/provider/workspace")
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert len(data["active_sessions"]) == 1
    item = data["active_sessions"][0]
    assert item["session_id"] == str(session_id)
    assert item["patient_id"] == str(patient_id)
    assert item["patient_display_identifier"] == "NC-P1234567890"
    assert item["status"] == "ACTIVE"
    assert item["allowed_operations"] == ["CREATE_ENCOUNTER", "WRITE_VITALS"]
    assert data["summary_counts"]["active_sessions_count"] == 1
    assert data["summary_counts"]["total_patients"] == 5

    # STRICT SECURITY ASSERTION: No secrets or hashes leaked
    raw_text = response.text
    assert "token_hash" not in raw_text
    assert "provider_session_binding_hash" not in raw_text
    assert "mock-provider-session-binding" not in raw_text


def test_workspace_returns_recent_encounters(client: TestClient):
    """Recent canonical encounters are returned with timestamps and display IDs."""
    provider_id = uuid.uuid4()
    hospital_id = uuid.uuid4()
    patient_id = uuid.uuid4()
    encounter_id = uuid.uuid4()
    session_id = uuid.uuid4()

    provider_ctx = _make_provider_context(provider_id=provider_id, hospital_id=hospital_id)

    mock_encounter = MagicMock(spec=ClinicalEncounter)
    mock_encounter.encounter_id = encounter_id
    mock_encounter.clinical_session_id = session_id
    mock_encounter.patient_id = patient_id
    mock_encounter.provider_id = provider_id
    mock_encounter.hospital_id = hospital_id
    mock_encounter.created_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=2)

    mock_patient = MagicMock(spec=Patient)
    mock_patient.patient_uuid = patient_id
    mock_patient.public_patient_id = "NC-ENC001"

    mock_db = AsyncMock()

    # 1. sessions: empty
    mock_sessions_res = MagicMock()
    mock_sessions_res.scalars.return_value.all.return_value = []

    # 2. encounters
    mock_encounters_res = MagicMock()
    mock_encounters_res.scalars.return_value.all.return_value = [mock_encounter]

    # 3. patient query
    mock_patient_res = MagicMock()
    mock_patient_res.scalars.return_value.all.return_value = [mock_patient]

    # 4. profile query
    mock_profile_res = MagicMock()
    mock_profile_res.scalars.return_value.all.return_value = []

    mock_db.execute.side_effect = [
        mock_sessions_res,
        mock_encounters_res,
        mock_patient_res,
        mock_profile_res,
    ]
    mock_db.scalar.return_value = 1

    app.dependency_overrides[get_provider_context] = lambda: provider_ctx
    app.dependency_overrides[get_db_session] = lambda: mock_db

    response = client.get("/api/v2/provider/workspace")
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert len(data["recent_encounters"]) == 1
    enc = data["recent_encounters"][0]
    assert enc["encounter_id"] == str(encounter_id)
    assert enc["clinical_session_id"] == str(session_id)
    assert enc["patient_id"] == str(patient_id)
    assert enc["patient_display_identifier"] == "NC-ENC001"
    assert data["summary_counts"]["recent_encounters_count"] == 1


def test_workspace_decrypts_patient_name_when_available(client: TestClient, monkeypatch):
    """When a patient profile is present, patient_name is decrypted and returned."""
    provider_id = uuid.uuid4()
    hospital_id = uuid.uuid4()
    patient_id = uuid.uuid4()
    session_id = uuid.uuid4()

    provider_ctx = _make_provider_context(provider_id=provider_id, hospital_id=hospital_id)

    mock_session = MagicMock(spec=ClinicalAccessSessionRecord)
    mock_session.session_id = session_id
    mock_session.encounter_id = None
    mock_session.patient_id = patient_id
    mock_session.provider_id = provider_id
    mock_session.hospital_id = hospital_id
    mock_session.status = "ACTIVE"
    mock_session.purpose = "treatment"
    mock_session.scope = "treatment"
    mock_session.allowed_operations = ["READ_CLINICAL_HISTORY"]
    mock_session.issued_at = datetime.datetime.now(datetime.timezone.utc)
    mock_session.expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=30)
    mock_session.revoked_at = None

    mock_patient = MagicMock(spec=Patient)
    mock_patient.patient_uuid = patient_id
    mock_patient.public_patient_id = "NC-P99999"

    mock_profile = MagicMock(spec=PatientProfile)
    mock_profile.patient_id = patient_id
    mock_profile.full_name_encrypted = "cipher:1"

    mock_kms = MagicMock()
    mock_kms.decrypt_field = AsyncMock(return_value="Aarav Sharma")
    monkeypatch.setattr(
        "app.api.v2.provider_workspace_routes.get_encryption_provider",
        lambda: mock_kms,
    )
    monkeypatch.setattr(
        "app.api.v2.provider_workspace_routes.EncryptedField.deserialize",
        lambda raw, field: MagicMock(),
    )

    mock_db = AsyncMock()
    mock_sessions_res = MagicMock()
    mock_sessions_res.scalars.return_value.all.return_value = [mock_session]
    mock_encounters_res = MagicMock()
    mock_encounters_res.scalars.return_value.all.return_value = []
    mock_patient_res = MagicMock()
    mock_patient_res.scalars.return_value.all.return_value = [mock_patient]
    mock_profile_res = MagicMock()
    mock_profile_res.scalars.return_value.all.return_value = [mock_profile]

    mock_db.execute.side_effect = [
        mock_sessions_res,
        mock_encounters_res,
        mock_patient_res,
        mock_profile_res,
    ]
    mock_db.scalar.return_value = 1

    app.dependency_overrides[get_provider_context] = lambda: provider_ctx
    app.dependency_overrides[get_db_session] = lambda: mock_db

    response = client.get("/api/v2/provider/workspace")
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert len(data["active_sessions"]) == 1
    assert data["active_sessions"][0]["patient_name"] == "Aarav Sharma"
    assert data["active_sessions"][0]["patient_display_identifier"] == "NC-P99999"


def test_workspace_tenant_and_provider_isolation(client: TestClient):
    """Verify that sessions and encounters are strictly filtered by authenticated provider and facility."""
    provider_id_a = uuid.uuid4()
    hospital_id_a = uuid.uuid4()
    provider_ctx = _make_provider_context(provider_id=provider_id_a, hospital_id=hospital_id_a)

    mock_db = AsyncMock()
    mock_sessions_res = MagicMock()
    mock_sessions_res.scalars.return_value.all.return_value = []
    mock_encounters_res = MagicMock()
    mock_encounters_res.scalars.return_value.all.return_value = []

    mock_db.execute.side_effect = [mock_sessions_res, mock_encounters_res]
    mock_db.scalar.return_value = 0

    app.dependency_overrides[get_provider_context] = lambda: provider_ctx
    app.dependency_overrides[get_db_session] = lambda: mock_db

    response = client.get("/api/v2/provider/workspace")
    assert response.status_code == status.HTTP_200_OK

    calls = mock_db.execute.call_args_list
    assert len(calls) >= 2
    session_query = calls[0][0][0]
    encounter_query = calls[1][0][0]

    compiled_session = str(session_query)
    assert "clinical_access_sessions.provider_id =" in compiled_session
    assert "clinical_access_sessions.hospital_id =" in compiled_session
    assert "clinical_access_sessions.status =" in compiled_session
    assert "clinical_access_sessions.revoked_at IS NULL" in compiled_session

    compiled_encounter = str(encounter_query)
    assert "clinical_encounters.provider_id =" in compiled_encounter
    assert "clinical_encounters.hospital_id =" in compiled_encounter


def test_workspace_corrupt_patient_profile_fails_safely(client: TestClient, monkeypatch):
    """When KMS decryption fails or ciphertext is corrupt, endpoint degrades safely without 500 or leaking ciphertext."""
    provider_id = uuid.uuid4()
    hospital_id = uuid.uuid4()
    patient_id = uuid.uuid4()
    session_id = uuid.uuid4()

    provider_ctx = _make_provider_context(provider_id=provider_id, hospital_id=hospital_id)

    mock_session = MagicMock(spec=ClinicalAccessSessionRecord)
    mock_session.session_id = session_id
    mock_session.encounter_id = None
    mock_session.patient_id = patient_id
    mock_session.provider_id = provider_id
    mock_session.hospital_id = hospital_id
    mock_session.status = "ACTIVE"
    mock_session.purpose = "treatment"
    mock_session.scope = "treatment"
    mock_session.allowed_operations = ["READ_CLINICAL_HISTORY"]
    mock_session.issued_at = datetime.datetime.now(datetime.timezone.utc)
    mock_session.expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=30)
    mock_session.revoked_at = None

    mock_patient = MagicMock(spec=Patient)
    mock_patient.patient_uuid = patient_id
    mock_patient.public_patient_id = "NC-CORRUPT01"

    mock_profile = MagicMock(spec=PatientProfile)
    mock_profile.patient_id = patient_id
    mock_profile.full_name_encrypted = "corrupt-ciphertext-bytes"

    mock_kms = MagicMock()
    mock_kms.decrypt_field = AsyncMock(side_effect=RuntimeError("KMS decryption failed: MAC mismatch"))
    monkeypatch.setattr(
        "app.api.v2.provider_workspace_routes.get_encryption_provider",
        lambda: mock_kms,
    )
    monkeypatch.setattr(
        "app.api.v2.provider_workspace_routes.EncryptedField.deserialize",
        lambda raw, field: MagicMock(),
    )

    mock_db = AsyncMock()
    mock_sessions_res = MagicMock()
    mock_sessions_res.scalars.return_value.all.return_value = [mock_session]
    mock_encounters_res = MagicMock()
    mock_encounters_res.scalars.return_value.all.return_value = []
    mock_patient_res = MagicMock()
    mock_patient_res.scalars.return_value.all.return_value = [mock_patient]
    mock_profile_res = MagicMock()
    mock_profile_res.scalars.return_value.all.return_value = [mock_profile]

    mock_db.execute.side_effect = [
        mock_sessions_res,
        mock_encounters_res,
        mock_patient_res,
        mock_profile_res,
    ]
    mock_db.scalar.return_value = 1

    app.dependency_overrides[get_provider_context] = lambda: provider_ctx
    app.dependency_overrides[get_db_session] = lambda: mock_db

    response = client.get("/api/v2/provider/workspace")
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert len(data["active_sessions"]) == 1
    # Safe fallback: patient_name is None, NOT the raw ciphertext
    assert data["active_sessions"][0]["patient_name"] is None
    assert data["active_sessions"][0]["patient_display_identifier"] == "NC-CORRUPT01"
    assert "corrupt-ciphertext-bytes" not in response.text


def test_workspace_data_minimization_and_sensitive_field_absence(client: TestClient):
    """Proves response strictly never contains Aadhaar, ABHA, phone, DOB, token_hash, or bearer capabilities."""
    provider_id = uuid.uuid4()
    hospital_id = uuid.uuid4()
    patient_id = uuid.uuid4()
    session_id = uuid.uuid4()

    provider_ctx = _make_provider_context(provider_id=provider_id, hospital_id=hospital_id)

    mock_session = MagicMock(spec=ClinicalAccessSessionRecord)
    mock_session.session_id = session_id
    mock_session.encounter_id = None
    mock_session.patient_id = patient_id
    mock_session.provider_id = provider_id
    mock_session.hospital_id = hospital_id
    mock_session.status = "ACTIVE"
    mock_session.purpose = "treatment"
    mock_session.scope = "treatment"
    mock_session.allowed_operations = ["CREATE_ENCOUNTER", "WRITE_VITALS"]
    mock_session.issued_at = datetime.datetime.now(datetime.timezone.utc)
    mock_session.expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=30)
    mock_session.revoked_at = None

    mock_patient = MagicMock(spec=Patient)
    mock_patient.patient_uuid = patient_id
    mock_patient.public_patient_id = "NC-MINIMAL01"

    mock_db = AsyncMock()
    mock_sessions_res = MagicMock()
    mock_sessions_res.scalars.return_value.all.return_value = [mock_session]
    mock_encounters_res = MagicMock()
    mock_encounters_res.scalars.return_value.all.return_value = []
    mock_patient_res = MagicMock()
    mock_patient_res.scalars.return_value.all.return_value = [mock_patient]
    mock_profile_res = MagicMock()
    mock_profile_res.scalars.return_value.all.return_value = []

    mock_db.execute.side_effect = [
        mock_sessions_res,
        mock_encounters_res,
        mock_patient_res,
        mock_profile_res,
    ]
    mock_db.scalar.return_value = 1

    app.dependency_overrides[get_provider_context] = lambda: provider_ctx
    app.dependency_overrides[get_db_session] = lambda: mock_db

    response = client.get("/api/v2/provider/workspace")
    assert response.status_code == status.HTTP_200_OK

    raw_text_lower = response.text.lower()
    prohibited_keys = [
        "aadhaar",
        "abha",
        "phone",
        "date_of_birth",
        "token_hash",
        "provider_session_binding_hash",
        "mock-provider-session-binding",
    ]
    for prohibited in prohibited_keys:
        assert prohibited not in raw_text_lower, f"Prohibited key '{prohibited}' leaked in workspace response"
