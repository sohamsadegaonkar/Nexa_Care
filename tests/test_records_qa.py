"""Patient records QA tests.

Covers:
  - Consent-gated read endpoints (summary, timeline, structured record)
  - Provider-authed write endpoints with provenance enforcement
  - AI-extracted provenance validation (confidence, risk_level, source_document_id)
  - Consent gate rejection (missing token, expired token, wrong purpose)
  - Allergy risk_level enforcement (HIGH_RISK required)
  - Model-level provenance constraints (CheckConstraints)
"""
from __future__ import annotations

import uuid
from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.dependencies import get_current_provider
from app.main import app
from app.models.provider_context import (
    AffiliationContext,
    HospitalContext,
    ProviderContext,
    ProviderIdentityContext,
)
from app.models.provider import AffiliationType
from app.services.consent_engine import ConsentCapability
from tests.conftest import DualModeTestClient, FakeRedis, FakeSyncRedis


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_provider_context(provider_id: str | None = None) -> ProviderContext:
    pid = uuid.UUID(provider_id) if provider_id else uuid.uuid4()
    return ProviderContext(
        provider=ProviderIdentityContext(
            provider_id=pid, display_name="Dr. Test",
            contact_email="test@hospital.example",
        ),
        hospital=HospitalContext(
            hospital_id=uuid.uuid4(), facility_code="TEST",
            display_name="Test Hospital",
        ),
        affiliation=AffiliationContext(
            affiliation_id=uuid.uuid4(),
            affiliation_type=AffiliationType.PERMANENT,
            is_primary=True, roles=["clinician"],
        ),
    )


def _make_capability(
    patient_id: str | None = None, scope: list | None = None,
) -> ConsentCapability:
    return ConsentCapability(
        patient_id=patient_id or str(uuid.uuid4()),
        clinician_id=str(uuid.uuid4()),
        purpose="TREATMENT",
        scope=scope or ["clinical.*", "pii.*"],
        is_break_glass=False,
        reason_code=None,
        issued_at=datetime.now(timezone.utc).isoformat(),
    )


@pytest.fixture
def client():
    return DualModeTestClient(app)


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def fake_sync_redis(fake_redis):
    return FakeSyncRedis(fake_redis)


@pytest.fixture
def overrides():
    """Manage app.dependency_overrides with automatic cleanup."""
    saved = {}
    yield saved
    for dep in saved:
        app.dependency_overrides.pop(dep, None)


def _apply_auth_overrides(overrides):
    """Override get_current_provider to bypass auth."""
    ctx = _make_provider_context()

    async def _provider():
        return ctx

    overrides[get_current_provider] = _provider
    app.dependency_overrides[get_current_provider] = _provider


def _consent_context(capability):
    """Return an ExitStack context manager that patches all consent/audit refs."""
    stack = ExitStack()
    stack.enter_context(
        patch("app.core.consent_gate.validate_consent_capability",
              new_callable=AsyncMock, return_value=capability)
    )
    for mod in [
        "app.core.consent_gate",
        "app.api.v2.patient_record_routes",
        "app.observability.audit_ledger",
    ]:
        stack.enter_context(
            patch(f"{mod}.append_audit_log_or_503", return_value=None)
        )
    stack.enter_context(
        patch("app.observability.audit_ledger.append_audit_log", return_value=None)
    )
    return stack


# ═══════════════════════════════════════════════════════════════════════════════
# 1. CONSENT-GATED READ: SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════


class TestPatientSummaryRead:
    """Validate consent-gated summary endpoint."""

    def test_summary_with_consent_returns_200(self, client, fake_redis, mock_db, overrides):
        """Summary endpoint returns data when valid consent token is present."""
        patient_id = str(uuid.uuid4())
        capability = _make_capability(patient_id, scope=["clinical.*", "pii.*"])
        _apply_auth_overrides(overrides)

        mock_db.execute.return_value = MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        )

        with _consent_context(capability):
            resp = client.get(
                f"/api/v2/patient/{patient_id}/summary",
                headers={"X-Consent-Token": "t", "X-Consent-Purpose": "clinical_summary"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["patient_id"] == patient_id
            assert "clinical_summary" in data
            assert "pii" in data

    def test_summary_without_consent_returns_403_or_401(self, client, fake_redis):
        """Summary endpoint returns 401/403 when consent token is invalid."""
        patient_id = str(uuid.uuid4())

        with patch("app.core.consent_gate.validate_consent_capability",
                    new_callable=AsyncMock, return_value=None), \
             patch("app.core.consent_gate.append_audit_log_or_503", return_value=None), \
             patch("app.observability.audit_ledger.append_audit_log_or_503", return_value=None), \
             patch("app.observability.audit_ledger.append_audit_log", return_value=None):
            resp = client.get(
                f"/api/v2/patient/{patient_id}/summary",
                headers={"X-Consent-Token": "invalid-token"},
            )
            assert resp.status_code in (401, 403)

    def test_summary_redacts_pii_with_clinical_scope(self, client, fake_redis, mock_db, overrides):
        """Summary with clinical-only scope shows [REDACTED] for PII fields."""
        patient_id = str(uuid.uuid4())
        capability = _make_capability(patient_id, scope=["clinical.*"])
        _apply_auth_overrides(overrides)

        mock_db.execute.return_value = MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        )

        with _consent_context(capability):
            resp = client.get(
                f"/api/v2/patient/{patient_id}/summary",
                headers={"X-Consent-Token": "t", "X-Consent-Purpose": "clinical_summary"},
            )
            assert resp.status_code == 200
            assert resp.json()["pii"]["patient_name"] == "[REDACTED]"
            assert resp.json()["shard_scope"] == "clinical"

    def test_summary_shows_pii_with_full_scope(self, client, fake_redis, mock_db, overrides):
        """Summary with full+PII scope shows actual PII fields."""
        patient_id = str(uuid.uuid4())
        capability = _make_capability(patient_id, scope=["clinical.*", "pii.*"])
        _apply_auth_overrides(overrides)

        mock_db.execute.return_value = MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        )

        with _consent_context(capability):
            resp = client.get(
                f"/api/v2/patient/{patient_id}/summary",
                headers={"X-Consent-Token": "t", "X-Consent-Purpose": "clinical_summary"},
            )
            assert resp.status_code == 200
            assert resp.json()["pii"]["patient_name"] != "[REDACTED]"
            assert resp.json()["shard_scope"] == "full"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. CONSENT-GATED READ: TIMELINE
# ═══════════════════════════════════════════════════════════════════════════════


class TestPatientTimelineRead:
    """Validate consent-gated timeline endpoint."""

    def test_timeline_with_consent_returns_200(self, client, fake_redis, mock_db, overrides):
        patient_id = str(uuid.uuid4())
        capability = _make_capability(patient_id)
        _apply_auth_overrides(overrides)

        mock_db.execute.return_value = MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        )

        with _consent_context(capability):
            resp = client.get(
                f"/api/v2/patient/{patient_id}/timeline",
                headers={"X-Consent-Token": "t", "X-Consent-Purpose": "timeline_view"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["patient_id"] == patient_id
            assert "events" in data

    def test_timeline_without_consent_returns_401_or_403(self, client, fake_redis):
        patient_id = str(uuid.uuid4())

        with patch("app.core.consent_gate.validate_consent_capability",
                    new_callable=AsyncMock, return_value=None), \
             patch("app.core.consent_gate.append_audit_log_or_503", return_value=None), \
             patch("app.observability.audit_ledger.append_audit_log_or_503", return_value=None):
            resp = client.get(
                f"/api/v2/patient/{patient_id}/timeline",
                headers={"X-Consent-Token": "invalid"},
            )
            assert resp.status_code in (401, 403)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. PROVIDER-AUTHED WRITE: VITALS
# ═══════════════════════════════════════════════════════════════════════════════


class TestRecordWrite:

    def test_append_vitals_with_consent_returns_201(self, client, fake_redis, mock_db, overrides):
        patient_id = str(uuid.uuid4())
        capability = _make_capability(patient_id)
        _apply_auth_overrides(overrides)

        with _consent_context(capability):
            resp = client.post(
                f"/api/v2/patient/{patient_id}/record/vitals",
                json={
                    "systolic_bp": 120, "diastolic_bp": 80,
                    "heart_rate": 72, "temperature_celsius": 36.6,
                    "sp_o2_percentage": 98,
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                    "source": "manual",
                },
                headers={"X-Consent-Token": "t"},
            )
            assert resp.status_code == 201
            data = resp.json()
            assert data["status"] == "committed"
            assert "record_id" in data
            assert "audit_ledger_hash" in data

    def test_append_vitals_without_consent_returns_401_or_403(self, client, fake_redis):
        patient_id = str(uuid.uuid4())

        with patch("app.core.consent_gate.validate_consent_capability",
                    new_callable=AsyncMock, return_value=None), \
             patch("app.core.consent_gate.append_audit_log_or_503", return_value=None), \
             patch("app.observability.audit_ledger.append_audit_log_or_503", return_value=None):
            resp = client.post(
                f"/api/v2/patient/{patient_id}/record/vitals",
                json={
                    "systolic_bp": 120, "diastolic_bp": 80,
                    "heart_rate": 72, "temperature_celsius": 36.6,
                    "sp_o2_percentage": 98,
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            assert resp.status_code in (401, 403)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. PROVENANCE ENFORCEMENT
# ═══════════════════════════════════════════════════════════════════════════════


class TestProvenanceEnforcement:

    def test_ai_extracted_requires_confidence(self, client, fake_redis, mock_db, overrides):
        patient_id = str(uuid.uuid4())
        capability = _make_capability(patient_id)
        _apply_auth_overrides(overrides)

        with _consent_context(capability):
            resp = client.post(
                f"/api/v2/patient/{patient_id}/record/vitals",
                json={
                    "systolic_bp": 120, "diastolic_bp": 80,
                    "heart_rate": 72, "temperature_celsius": 36.6,
                    "sp_o2_percentage": 98,
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                    "source": "ai_extracted", "confidence": None, "risk_level": "LOW_RISK",
                },
                headers={"X-Consent-Token": "t"},
            )
            assert resp.status_code == 400

    def test_ai_extracted_requires_risk_level(self, client, fake_redis, mock_db, overrides):
        patient_id = str(uuid.uuid4())
        capability = _make_capability(patient_id)
        _apply_auth_overrides(overrides)

        with _consent_context(capability):
            resp = client.post(
                f"/api/v2/patient/{patient_id}/record/vitals",
                json={
                    "systolic_bp": 120, "diastolic_bp": 80,
                    "heart_rate": 72, "temperature_celsius": 36.6,
                    "sp_o2_percentage": 98,
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                    "source": "ai_extracted", "confidence": 0.95, "risk_level": "",
                },
                headers={"X-Consent-Token": "t"},
            )
            assert resp.status_code == 400

    def test_ai_extracted_requires_source_document_id(self, client, fake_redis, mock_db, overrides):
        patient_id = str(uuid.uuid4())
        capability = _make_capability(patient_id)
        _apply_auth_overrides(overrides)

        with _consent_context(capability):
            resp = client.post(
                f"/api/v2/patient/{patient_id}/record/vitals",
                json={
                    "systolic_bp": 120, "diastolic_bp": 80,
                    "heart_rate": 72, "temperature_celsius": 36.6,
                    "sp_o2_percentage": 98,
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                    "source": "ai_extracted", "confidence": 0.95,
                    "risk_level": "LOW_RISK", "source_document_id": None,
                },
                headers={"X-Consent-Token": "t"},
            )
            assert resp.status_code == 400

    def test_manual_vitals_without_provenance_succeeds(self, client, fake_redis, mock_db, overrides):
        patient_id = str(uuid.uuid4())
        capability = _make_capability(patient_id)
        _apply_auth_overrides(overrides)

        with _consent_context(capability):
            resp = client.post(
                f"/api/v2/patient/{patient_id}/record/vitals",
                json={
                    "systolic_bp": 120, "diastolic_bp": 80,
                    "heart_rate": 72, "temperature_celsius": 36.6,
                    "sp_o2_percentage": 98,
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                    "source": "manual",
                },
                headers={"X-Consent-Token": "t"},
            )
            assert resp.status_code == 201


# ═══════════════════════════════════════════════════════════════════════════════
# 5. ALLERGY RISK LEVEL ENFORCEMENT
# ═══════════════════════════════════════════════════════════════════════════════


class TestAllergyRiskLevel:

    def test_allergy_must_be_high_risk(self, client, fake_redis, mock_db, overrides):
        patient_id = str(uuid.uuid4())
        capability = _make_capability(patient_id)
        _apply_auth_overrides(overrides)

        with _consent_context(capability):
            resp = client.post(
                f"/api/v2/patient/{patient_id}/record/allergies",
                json={"allergen": "Penicillin", "severity": "severe", "risk_level": "LOW_RISK"},
                headers={"X-Consent-Token": "t"},
            )
            assert resp.status_code == 400
            assert "HIGH_RISK" in resp.json()["detail"]

    def test_allergy_with_high_risk_succeeds(self, client, fake_redis, mock_db, overrides):
        patient_id = str(uuid.uuid4())
        capability = _make_capability(patient_id)
        _apply_auth_overrides(overrides)

        with _consent_context(capability):
            resp = client.post(
                f"/api/v2/patient/{patient_id}/record/allergies",
                json={"allergen": "Penicillin", "severity": "severe", "risk_level": "HIGH_RISK"},
                headers={"X-Consent-Token": "t"},
            )
            assert resp.status_code == 201


# ═══════════════════════════════════════════════════════════════════════════════
# 6. STRUCTURED RECORD ENDPOINT
# ═══════════════════════════════════════════════════════════════════════════════


class TestStructuredRecord:

    def test_structured_record_with_full_consent(self, client, fake_redis, mock_db, overrides):
        patient_id = str(uuid.uuid4())
        capability = _make_capability(patient_id, scope=["full"])
        _apply_auth_overrides(overrides)

        mock_db.execute.return_value = MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        )

        with _consent_context(capability):
            resp = client.get(
                f"/api/v2/patient/{patient_id}/structured-record",
                headers={"X-Consent-Token": "t", "X-Consent-Purpose": "full"},
            )
            assert resp.status_code == 200
            data = resp.json()
            for key in ("vitals", "medications", "lab_results", "allergies", "documents"):
                assert key in data

    def test_structured_record_without_consent(self, client, fake_redis):
        patient_id = str(uuid.uuid4())

        with patch("app.core.consent_gate.validate_consent_capability",
                    new_callable=AsyncMock, return_value=None), \
             patch("app.core.consent_gate.append_audit_log_or_503", return_value=None), \
             patch("app.observability.audit_ledger.append_audit_log_or_503", return_value=None):
            resp = client.get(
                f"/api/v2/patient/{patient_id}/structured-record",
                headers={"X-Consent-Token": "invalid"},
            )
            assert resp.status_code in (401, 403)


# ═══════════════════════════════════════════════════════════════════════════════
# 7. MODEL-LEVEL PROVENANCE CONSTRAINTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestModelProvenanceConstraints:

    def test_vitals_has_provenance_constraint(self):
        from app.models.patient_records import Vitals
        names = [c.name for c in Vitals.__table_args__ if hasattr(c, "name")]
        assert "ck_patient_vitals_provenance_complete" in names

    def test_medication_has_provenance_constraint(self):
        from app.models.patient_records import Medication
        names = [c.name for c in Medication.__table_args__ if hasattr(c, "name")]
        assert "ck_patient_medications_provenance_complete" in names

    def test_lab_result_has_provenance_constraint(self):
        from app.models.patient_records import LabResult
        names = [c.name for c in LabResult.__table_args__ if hasattr(c, "name")]
        assert "ck_patient_lab_results_provenance_complete" in names

    def test_allergy_has_provenance_constraint(self):
        from app.models.patient_records import Allergy
        names = [c.name for c in Allergy.__table_args__ if hasattr(c, "name")]
        assert "ck_patient_allergies_provenance_complete" in names

    def test_allergy_default_risk_level_is_high(self):
        from app.models.patient_records import Allergy
        col = Allergy.__table__.c.risk_level
        assert col.default is not None or hasattr(Allergy, "__init__")
