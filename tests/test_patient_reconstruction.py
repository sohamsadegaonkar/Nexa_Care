"""Tests for consent-scoped reconstruction endpoint sequencing."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.core.database import get_db_session
from app.core.dependencies import get_current_provider
from app.main import app
from app.api.v2.patient_routes import get_kms_provider
from app.models.provider import AffiliationType
from app.models.provider_context import (
    AffiliationContext,
    HospitalContext,
    ProviderContext,
    ProviderIdentityContext,
)
from app.services.consent_engine import ConsentCapability


def sample_provider_context() -> ProviderContext:
    return ProviderContext(
        provider=ProviderIdentityContext(
            provider_id=uuid.uuid4(),
            display_name="Dr. Reconstruction Test",
            medical_registration_number="MCI-REC-1",
            specialty="General Medicine",
            contact_email="reconstruction@example.com",
        ),
        hospital=HospitalContext(
            hospital_id=uuid.uuid4(),
            facility_code="REC-HOSP",
            display_name="Reconstruction Test Hospital",
        ),
        affiliation=AffiliationContext(
            affiliation_id=uuid.uuid4(),
            affiliation_type=AffiliationType.PERMANENT,
            department="OPD",
            roles=["routine_reader"],
            is_primary=True,
            valid_from=None,
            valid_until=None,
        ),
    )


class FakeScalarResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class FakeExecuteResult:
    def __init__(self, row):
        self._row = row

    def scalars(self):
        return FakeScalarResult(self._row)


ENCRYPTED_TEST_FIELD = "MDEyMzQ1Njc4OTAxMg==:1"


class FakeKMSProvider:
    async def decrypt_field(self, patient_id: str, field_name: str, encrypted, db):
        if field_name == "patient_name":
            return "Jane Doe"
        if field_name == "diagnoses":
            return ["Diabetes"]
        raise AssertionError(f"unexpected decrypt field: {field_name}")


class FakeDBSession:
    def __init__(self) -> None:
        self.calls = []

    async def execute(self, stmt):
        stmt_text = str(stmt)
        self.calls.append(stmt_text)
        if "nexa_vault" in stmt_text:
            return FakeExecuteResult(SimpleNamespace(
                raw_pii={"patient_name": "Jane Doe", "phone": "9999999999"},
                patient_name=ENCRYPTED_TEST_FIELD,
                phone=None,
                aadhaar_abha_id=None,
            ))
        if "nexa_clinical" in stmt_text:
            return FakeExecuteResult(SimpleNamespace(
                clinical_data={},
                diagnoses=ENCRYPTED_TEST_FIELD,
                lab_results=None,
                prescriptions=None,
            ))
        raise AssertionError(f"unexpected query: {stmt_text}")


class AuditContext:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def __aenter__(self):
        self.events.append("audit_enter")
        return "audit-id"

    async def __aexit__(self, exc_type, exc, tb):
        self.events.append("audit_exit")
        return False


def test_reconstruction_validates_before_and_inside_audit_then_consumes() -> None:
    provider = sample_provider_context()
    patient_id = uuid.uuid4()
    fake_db = FakeDBSession()
    events: list[str] = []
    capability = ConsentCapability(
        patient_id=str(patient_id),
        clinician_id=provider.actor_uid,
        purpose="treatment",
        scope=["pii.patient_name", "clinical.diagnoses"],
        is_break_glass=False,
        reason_code=None,
        issued_at="2026-07-02T00:00:00+00:00",
    )

    async def override_provider() -> ProviderContext:
        return provider

    async def override_db():
        yield fake_db

    async def validate_side_effect(**kwargs):
        events.append("validate")
        return capability

    async def consume_side_effect(**kwargs):
        events.append("consume")
        return capability

    app.dependency_overrides[get_current_provider] = override_provider
    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_kms_provider] = lambda: FakeKMSProvider()

    try:
        with (
            patch("app.api.v2.patient_routes.consent_engine.validate", new_callable=AsyncMock) as mock_validate,
            patch("app.api.v2.patient_routes.consent_engine.consume", new_callable=AsyncMock) as mock_consume,
            patch("app.services.consent_gated_crypto.append_audit_log_or_503", new_callable=AsyncMock) as mock_audit,
       ):
            async def audit_side_effect(*, audit_context, actor_uid, event_type, target_id, status, metadata=None, event_timestamp=None):
                events.append(f"audit:{event_type}")

            mock_validate.side_effect = validate_side_effect
            mock_consume.side_effect = consume_side_effect
            mock_audit.side_effect = audit_side_effect
            client = TestClient(app)

            response = client.get(
                f"/api/v2/patient/{patient_id}/record",
                headers={
                    "Authorization": "Bearer provider-session",
                    "X-Consent-Token": "routine-token",
                    "X-Consent-Purpose": "treatment",
                },
            )
    finally:
        app.dependency_overrides.pop(get_current_provider, None)
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(get_kms_provider, None)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "pii": {"patient_name": "Jane Doe"},
        "clinical": {"diagnoses": ["Diabetes"]},
    }
    assert events == [
        "validate",
        "audit:CONSENT_GATED_DECRYPT_STARTED",
        "consume",
        "audit:CONSENT_GATED_DECRYPT_COMPLETED",
    ]
    assert mock_validate.await_count == 1
    assert mock_validate.await_args.kwargs["hospital_id"] == str(provider.hospital_id)
    assert mock_consume.await_count == 1
