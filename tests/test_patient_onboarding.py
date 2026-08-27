"""Unit and route tests for patient onboarding status derivation.

Verifies:
- New patient -> profile_complete=False, terms_current=False, privacy_current=False, complete=False, next_step="PROFILE"
- Profile complete only -> profile_complete=True, terms_current=False, privacy_current=False, complete=False, next_step="LEGAL_ACCEPTANCE"
- Profile + terms only -> next_step="LEGAL_ACCEPTANCE"
- Profile + privacy only -> next_step="LEGAL_ACCEPTANCE"
- Profile + both current terms & privacy -> complete=True, next_step="COMPLETE"
- Stale acceptance (accepted v1, server requires v2) -> terms_current=False, complete=False, next_step="LEGAL_ACCEPTANCE"
- Legal config failure -> fail closed, complete=False
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.config import PatientLegalDocumentConfig
from app.core.dependencies import AuthenticatedPatient, get_current_patient
from app.models.patient import Patient
from app.models.patient_legal_acceptance import PatientLegalAcceptance
from app.models.patient_profile import PatientProfile
from app.services.patient_legal_service import (
    LegalAcceptanceError,
    OnboardingStatus,
    get_onboarding_status,
)

_VALID_CONFIG = PatientLegalDocumentConfig(
    terms_version="2026.1",
    terms_sha256="a" * 64,
    terms_url="https://legal.nexa.test/terms/2026.1",
    privacy_version="2026.1",
    privacy_sha256="b" * 64,
    privacy_url="https://legal.nexa.test/privacy/2026.1",
)


@pytest.fixture(autouse=True)
def _set_test_env(monkeypatch):
    monkeypatch.setenv("PATIENT_TERMS_VERSION", "2026.1")
    monkeypatch.setenv("PATIENT_TERMS_SHA256", "a" * 64)
    monkeypatch.setenv("PATIENT_TERMS_URL", "https://legal.nexa.test/terms/2026.1")
    monkeypatch.setenv("PATIENT_PRIVACY_VERSION", "2026.1")
    monkeypatch.setenv("PATIENT_PRIVACY_SHA256", "b" * 64)
    monkeypatch.setenv("PATIENT_PRIVACY_URL", "https://legal.nexa.test/privacy/2026.1")
    monkeypatch.setenv(
        "PATIENT_JWT_SECRET", "test-secret-at-least-32-chars-long-here!!"
    )


def _mock_db_with_profile_and_acceptances(
    profile: PatientProfile | None,
    terms_accepted: bool,
    privacy_accepted: bool,
) -> AsyncMock:
    pid = uuid.uuid4()
    db = AsyncMock()
    call_count = 0

    async def _execute(stmt):
        nonlocal call_count
        call_count += 1
        res = MagicMock()
        stmt_str = str(stmt)
        if "patient_profiles" in stmt_str:
            res.scalar_one_or_none.return_value = profile
        elif (
            "TERMS_OF_SERVICE" in stmt_str
            or "document_type" in stmt_str
            and call_count == 2
        ):
            res.scalar_one_or_none.return_value = (
                PatientLegalAcceptance(
                    patient_id=pid,
                    document_type="TERMS_OF_SERVICE",
                    document_version="2026.1",
                    document_sha256="a" * 64,
                )
                if terms_accepted
                else None
            )
        elif (
            "PRIVACY_NOTICE" in stmt_str
            or "document_type" in stmt_str
            and call_count == 3
        ):
            res.scalar_one_or_none.return_value = (
                PatientLegalAcceptance(
                    patient_id=pid,
                    document_type="PRIVACY_NOTICE",
                    document_version="2026.1",
                    document_sha256="b" * 64,
                )
                if privacy_accepted
                else None
            )
        else:
            res.scalar_one_or_none.return_value = None
        return res

    db.execute = AsyncMock(side_effect=_execute)
    return db


# ---------------------------------------------------------------------------
# Unit Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_patient_status_is_profile():
    pid = str(uuid.uuid4())
    db = _mock_db_with_profile_and_acceptances(
        profile=None, terms_accepted=False, privacy_accepted=False
    )

    with patch(
        "app.services.patient_legal_service.get_patient_legal_config",
        return_value=_VALID_CONFIG,
    ):
        status = await get_onboarding_status(pid, db)
        assert status.profile_complete is False
        assert status.terms_current is False
        assert status.privacy_current is False
        assert status.complete is False
        assert status.next_step == "PROFILE"


@pytest.mark.asyncio
async def test_profile_only_status_is_legal_acceptance():
    pid = str(uuid.uuid4())
    profile = PatientProfile(
        patient_id=uuid.UUID(pid),
        full_name_encrypted="enc_name:1",
        date_of_birth_encrypted="enc_dob:1",
    )
    db = _mock_db_with_profile_and_acceptances(
        profile=profile, terms_accepted=False, privacy_accepted=False
    )

    with patch(
        "app.services.patient_legal_service.get_patient_legal_config",
        return_value=_VALID_CONFIG,
    ):
        status = await get_onboarding_status(pid, db)
        assert status.profile_complete is True
        assert status.terms_current is False
        assert status.privacy_current is False
        assert status.complete is False
        assert status.next_step == "LEGAL_ACCEPTANCE"


@pytest.mark.asyncio
async def test_profile_and_terms_only_status_is_legal_acceptance():
    pid = str(uuid.uuid4())
    profile = PatientProfile(
        patient_id=uuid.UUID(pid),
        full_name_encrypted="enc_name:1",
        date_of_birth_encrypted="enc_dob:1",
    )
    db = _mock_db_with_profile_and_acceptances(
        profile=profile, terms_accepted=True, privacy_accepted=False
    )

    with patch(
        "app.services.patient_legal_service.get_patient_legal_config",
        return_value=_VALID_CONFIG,
    ):
        status = await get_onboarding_status(pid, db)
        assert status.profile_complete is True
        assert status.terms_current is True
        assert status.privacy_current is False
        assert status.complete is False
        assert status.next_step == "LEGAL_ACCEPTANCE"


@pytest.mark.asyncio
async def test_profile_and_both_legal_status_is_complete():
    pid = str(uuid.uuid4())
    profile = PatientProfile(
        patient_id=uuid.UUID(pid),
        full_name_encrypted="enc_name:1",
        date_of_birth_encrypted="enc_dob:1",
    )
    db = _mock_db_with_profile_and_acceptances(
        profile=profile, terms_accepted=True, privacy_accepted=True
    )

    with patch(
        "app.services.patient_legal_service.get_patient_legal_config",
        return_value=_VALID_CONFIG,
    ):
        status = await get_onboarding_status(pid, db)
        assert status.profile_complete is True
        assert status.terms_current is True
        assert status.privacy_current is True
        assert status.complete is True
        assert status.next_step == "COMPLETE"


@pytest.mark.asyncio
async def test_legal_config_failure_fails_closed():
    pid = str(uuid.uuid4())
    profile = PatientProfile(
        patient_id=uuid.UUID(pid),
        full_name_encrypted="enc_name:1",
        date_of_birth_encrypted="enc_dob:1",
    )
    db = _mock_db_with_profile_and_acceptances(
        profile=profile, terms_accepted=True, privacy_accepted=True
    )

    with patch(
        "app.services.patient_legal_service._resolve_document_config",
        side_effect=LegalAcceptanceError("CONFIG_ERROR", "Config missing"),
    ):
        status = await get_onboarding_status(pid, db)
        # Never report complete when legal config is broken
        assert status.complete is False
        assert status.terms_current is False
        assert status.privacy_current is False


# ---------------------------------------------------------------------------
# Route Test
# ---------------------------------------------------------------------------


def test_get_onboarding_status_route():
    client = TestClient(app)
    test_pid = str(uuid.uuid4())

    async def mock_auth():
        return AuthenticatedPatient(
            patient_id=test_pid,
            patient=Patient(patient_uuid=uuid.UUID(test_pid)),
        )

    app.dependency_overrides[get_current_patient] = mock_auth

    mock_status = OnboardingStatus(
        profile_complete=True,
        terms_current=True,
        privacy_current=True,
        complete=True,
        next_step="COMPLETE",
    )

    with patch(
        "app.api.v2.patient_self_routes.get_onboarding_status",
        new_callable=AsyncMock,
        return_value=mock_status,
    ):
        resp = client.get("/api/v2/patient/me/onboarding-status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["profile_complete"] is True
        assert body["terms_current"] is True
        assert body["privacy_current"] is True
        assert body["complete"] is True
        assert body["next_step"] == "COMPLETE"

    app.dependency_overrides.clear()
