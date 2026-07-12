"""Security tests — T-08: Unsafe Auto-Approval.

Verifies that the canonical auto-approval engine enforces:
- CRITICAL_RISK fields are never auto-approved
- HIGH_RISK fields are never auto-approved
- Allergy/allergen fields are forced to HIGH_RISK and never auto-approved
- Low-confidence fields are forced to needs_review

These tests exercise the real should_auto_approve() function and
can_auto_approve() wrapper directly, plus the pipeline commit
endpoint's validation of auto_approved HIGH/CRITICAL fields.

Threat model reference: docs/threat-model.md T-08
"""

from __future__ import annotations

import uuid
from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ai.auto_approval import should_auto_approve
from app.ai.medical_validator import validate_field
from app.models.extracted_field import ExtractedField
from app.services.pipeline_safety import can_auto_approve
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


def _make_provider_context() -> ProviderContext:
    return ProviderContext(
        provider=ProviderIdentityContext(
            provider_id=uuid.uuid4(),
            display_name="Dr. Safety",
            contact_email="safety@hospital.example",
        ),
        hospital=HospitalContext(
            hospital_id=uuid.uuid4(),
            facility_code="SAF",
            display_name="Safety Hospital",
        ),
        affiliation=AffiliationContext(
            affiliation_id=uuid.uuid4(),
            affiliation_type=AffiliationType.PERMANENT,
            is_primary=True,
            roles=["clinician"],
        ),
    )


def _make_capability(patient_id: str) -> ConsentCapability:
    return ConsentCapability(
        patient_id=patient_id,
        clinician_id=str(uuid.uuid4()),
        purpose="TREATMENT",
        scope=["clinical.*", "pii.*"],
        is_break_glass=False,
        reason_code=None,
        issued_at=datetime.now(timezone.utc).isoformat(),
    )


def _db_result(*, scalar_one_or_none=None, scalars_all=None, scalar=None):
    if scalars_all is not None:
        return MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=scalars_all))),
        )
    if scalar is not None:
        return MagicMock(scalar=MagicMock(return_value=scalar))
    return MagicMock(scalar_one_or_none=MagicMock(return_value=scalar_one_or_none))


def _side_effect_with_fallback(results):
    default = MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
        scalar_one_or_none=MagicMock(return_value=None),
        scalar=MagicMock(return_value=0),
    )
    results_iter = iter(results)

    def _next(*args, **kwargs):
        try:
            return next(results_iter)
        except StopIteration:
            return default

    return _next


def _reset_mock_db(mock_db):
    mock_db.execute.side_effect = None
    mock_db.execute.reset_mock()
    mock_db.execute.return_value = MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
        scalar_one_or_none=MagicMock(return_value=None),
        scalar=MagicMock(return_value=0),
    )


# ── Fixtures ─────────────────────────────────────────────────────────────────


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
    saved = {}
    yield saved
    for dep in saved:
        app.dependency_overrides.pop(dep, None)


# ═══════════════════════════════════════════════════════════════════════════════
# AUTO-APPROVAL ENGINE TESTS (direct, no HTTP)
# ═══════════════════════════════════════════════════════════════════════════════


def test_critical_risk_never_auto_approved():
    """T-08a: CRITICAL_RISK fields are never auto-approved, regardless of confidence."""
    field = ExtractedField(
        field_name="critical_value",
        raw_value="SEVERE_HYPOGLYCEMIA",
        confidence=0.99,
        risk_level="CRITICAL_RISK",
        status="auto_approved",  # model allows but engine must override
    )
    decision = should_auto_approve(field)
    assert not decision.auto_approve, (
        f"CRITICAL_RISK must never be auto-approved, but got: {decision.reason}"
    )
    assert "CRITICAL_RISK" in decision.reason or "HIGH_RISK" in decision.reason


def test_high_risk_never_auto_approved():
    """T-08b: HIGH_RISK fields are never auto-approved, regardless of confidence."""
    field = ExtractedField(
        field_name="medication_interaction",
        raw_value="Warfarin + Aspirin",
        confidence=0.98,
        risk_level="HIGH_RISK",
        status="auto_approved",
    )
    decision = should_auto_approve(field)
    assert not decision.auto_approve, (
        f"HIGH_RISK must never be auto-approved, but got: {decision.reason}"
    )


def test_allergy_always_to_review():
    """T-08c: Allergy/allergen fields are forced to HIGH_RISK and never auto-approved."""
    field = ExtractedField(
        field_name="allergy",
        raw_value="Penicillin",
        confidence=0.99,
        risk_level="LOW_RISK",  # even if scorer says LOW_RISK
        status="auto_approved",
    )
    decision = should_auto_approve(field)
    assert not decision.auto_approve, (
        "Allergy fields must never be auto-approved"
    )
    # The engine must force risk_level to HIGH_RISK
    assert field.risk_level == "HIGH_RISK", (
        "Allergy fields must be forced to HIGH_RISK by the auto-approval engine"
    )


def test_allergen_always_to_review():
    """T-08d: 'allergen' field name is also forced to review."""
    field = ExtractedField(
        field_name="allergen",
        raw_value="Latex",
        confidence=0.95,
        risk_level="MEDIUM_RISK",
        status="auto_approved",
    )
    decision = should_auto_approve(field)
    assert not decision.auto_approve
    assert field.risk_level == "HIGH_RISK"


def test_low_confidence_forces_needs_review():
    """T-08e: Low confidence (< threshold) forces needs_review."""
    field = ExtractedField(
        field_name="bp",
        raw_value="120/80",
        confidence=0.30,  # well below LOW_RISK threshold of 0.95
        risk_level="LOW_RISK",
        status="auto_approved",
    )
    decision = should_auto_approve(field)
    assert not decision.auto_approve, (
        f"Low-confidence field must not be auto-approved, got: {decision.reason}"
    )
    assert "below threshold" in decision.reason.lower() or "confidence" in decision.reason.lower()


def test_medium_risk_below_threshold_forces_review():
    """T-08f: MEDIUM_RISK with confidence < 0.97 forces needs_review."""
    field = ExtractedField(
        field_name="lab_value",
        raw_value="5.6",
        confidence=0.94,  # below MEDIUM_RISK threshold of 0.97
        risk_level="MEDIUM_RISK",
        status="auto_approved",
    )
    decision = should_auto_approve(field)
    assert not decision.auto_approve


def test_low_risk_above_threshold_auto_approves():
    """T-08g: LOW_RISK with confidence >= 0.95 IS auto-approved (positive case)."""
    field = ExtractedField(
        field_name="bp",
        raw_value="120/80",
        confidence=0.96,
        risk_level="LOW_RISK",
    )
    decision = should_auto_approve(field)
    assert decision.auto_approve


def test_can_auto_approve_delegates_correctly():
    """T-08h: can_auto_approve() wrapper delegates to should_auto_approve()."""
    field = ExtractedField(
        field_name="test",
        raw_value="x",
        confidence=0.50,
        risk_level="HIGH_RISK",
    )
    # Both must agree
    assert can_auto_approve(field) == should_auto_approve(field).auto_approve
    assert not can_auto_approve(field)


def test_missing_confidence_forces_review():
    """T-08i: Field with confidence=None cannot be auto-approved."""
    field = ExtractedField(
        field_name="unknown_field",
        raw_value="unknown",
        confidence=None,
        risk_level="LOW_RISK",
    )
    decision = should_auto_approve(field)
    assert not decision.auto_approve, (
        "Field without confidence must not be auto-approved"
    )




def test_unknown_reference_lab_never_auto_approved():
    """T-08l: Unknown lab reference ranges are not eligible for auto-approval."""
    field = ExtractedField(
        field_name="lab_result",
        raw_value="450 mg/dL",
        confidence=0.99,
        risk_level="LOW_RISK",
        status="auto_approved",
        validation_result=validate_field("lab_result", "450 mg/dL"),
    )
    decision = should_auto_approve(field)
    assert not decision.auto_approve
    assert "validation" in decision.reason.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# PIPELINE COMMIT ENDPOINT TESTS (HTTP, with mock DB)
# ═══════════════════════════════════════════════════════════════════════════════


def test_commit_rejects_high_risk_auto_approved_field(
    client, fake_redis, fake_sync_redis, mock_db, overrides,
):
    """T-08j: Pipeline commit rejects a field with status=auto_approved + risk_level=HIGH_RISK.

    The commit endpoint must validate that auto_approved fields are
    not HIGH_RISK or CRITICAL_RISK. This is defense-in-depth: even
    if the pipeline engine incorrectly marks a field, the commit
    endpoint catches it.
    """
    patient_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    provider = _make_provider_context()
    capability = _make_capability(patient_id)

    async def _provider_dep():
        return provider

    overrides[get_current_provider] = _provider_dep
    app.dependency_overrides[get_current_provider] = _provider_dep

    job = MagicMock()
    job.id = uuid.UUID(job_id)
    job.patient_id = uuid.UUID(patient_id)
    job.status = "scored"
    job.document_type = "LAB_REPORT"

    with ExitStack() as stack:
        stack.enter_context(
            patch("app.core.consent_gate.validate_consent_capability",
                  new_callable=AsyncMock, return_value=capability)
        )
        for mod in (
            "app.core.consent_gate",
            "app.api.v2.pipeline_routes",
            "app.observability.audit_ledger",
            "app.services.record_ingestion",
            "app.services.pipeline_orchestrator",
        ):
            stack.enter_context(patch(f"{mod}.append_audit_log_or_503", return_value=None))
        stack.enter_context(patch("app.observability.audit_ledger.append_audit_log", return_value=None))
        stack.enter_context(patch("app.api.v2.pipeline_routes.process_extraction_job", return_value=None))
        stack.enter_context(patch("app.services.pipeline_orchestrator.process_extraction_job", return_value=None))
        stack.enter_context(
            patch("app.api.v2.pipeline_routes.ingest_extracted_fields",
                  new_callable=AsyncMock, return_value=None)
        )

        _reset_mock_db(mock_db)
        mock_db.execute.side_effect = _side_effect_with_fallback([
            _db_result(scalar_one_or_none=job),
            _db_result(scalars_all=[]),
        ])
        resp = client.post(
            f"/api/v2/pipeline/jobs/{job_id}/commit",
            json={
                "patient_id": patient_id,
                "fields": [{
                    "field_id": str(uuid.uuid4()),
                    "field_name": "medication_interaction",
                    "raw_value": "Warfarin",
                    "normalized_value": "Warfarin",
                    "confidence": 0.92,
                    "risk_level": "HIGH_RISK",
                    "status": "auto_approved",  # Should NOT be allowed
                }],
            },
            headers={"X-Consent-Token": "t"},
        )
        # The commit endpoint should reject this — either 400 or 409
        assert resp.status_code in (400, 409), (
            f"auto_approved HIGH_RISK field should be rejected at commit, "
            f"got {resp.status_code}: {resp.text}"
        )


def test_commit_rejects_critical_risk_auto_approved_field(
    client, fake_redis, fake_sync_redis, mock_db, overrides,
):
    """T-08k: Pipeline commit rejects auto_approved CRITICAL_RISK field."""
    patient_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    provider = _make_provider_context()
    capability = _make_capability(patient_id)

    async def _provider_dep():
        return provider

    overrides[get_current_provider] = _provider_dep
    app.dependency_overrides[get_current_provider] = _provider_dep

    job = MagicMock()
    job.id = uuid.UUID(job_id)
    job.patient_id = uuid.UUID(patient_id)
    job.status = "scored"

    with ExitStack() as stack:
        stack.enter_context(
            patch("app.core.consent_gate.validate_consent_capability",
                  new_callable=AsyncMock, return_value=capability)
        )
        for mod in (
            "app.core.consent_gate",
            "app.api.v2.pipeline_routes",
            "app.observability.audit_ledger",
            "app.services.record_ingestion",
            "app.services.pipeline_orchestrator",
        ):
            stack.enter_context(patch(f"{mod}.append_audit_log_or_503", return_value=None))
        stack.enter_context(patch("app.observability.audit_ledger.append_audit_log", return_value=None))
        stack.enter_context(patch("app.api.v2.pipeline_routes.process_extraction_job", return_value=None))
        stack.enter_context(patch("app.services.pipeline_orchestrator.process_extraction_job", return_value=None))
        stack.enter_context(
            patch("app.api.v2.pipeline_routes.ingest_extracted_fields",
                  new_callable=AsyncMock, return_value=None)
        )

        _reset_mock_db(mock_db)
        mock_db.execute.side_effect = _side_effect_with_fallback([
            _db_result(scalar_one_or_none=job),
            _db_result(scalars_all=[]),
        ])
        resp = client.post(
            f"/api/v2/pipeline/jobs/{job_id}/commit",
            json={
                "patient_id": patient_id,
                "fields": [{
                    "field_id": str(uuid.uuid4()),
                    "field_name": "critical_allergy",
                    "raw_value": "Penicillin",
                    "normalized_value": "Penicillin",
                    "confidence": 0.88,
                    "risk_level": "CRITICAL_RISK",
                    "status": "auto_approved",  # Must NOT be allowed
                }],
            },
            headers={"X-Consent-Token": "t"},
        )
        assert resp.status_code in (400, 409), (
            f"auto_approved CRITICAL_RISK field should be rejected, "
            f"got {resp.status_code}: {resp.text}"
        )
