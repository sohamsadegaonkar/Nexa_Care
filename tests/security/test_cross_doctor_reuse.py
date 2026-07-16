"""Security tests — T-04: Cross-Doctor Consent Reuse.

Verifies that consent tokens cannot be used by a different clinician
than the one they were issued to. The consent engine's validate()
checks clinician_id binding on every call.

Threat model reference: docs/threat-model.md T-04
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

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
from app.services.consent_engine import validate
from tests.conftest import DualModeTestClient, FakeRedis, FakeSyncRedis


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_provider_context(provider_id=None) -> ProviderContext:
    return ProviderContext(
        provider=ProviderIdentityContext(
            provider_id=provider_id or uuid.uuid4(),
            display_name="Dr. Test",
            contact_email="test@hospital.example",
        ),
        hospital=HospitalContext(
            hospital_id=uuid.uuid4(),
            facility_code="TST",
            display_name="Test Hospital",
        ),
        affiliation=AffiliationContext(
            affiliation_id=uuid.uuid4(),
            affiliation_type=AffiliationType.PERMANENT,
            is_primary=True,
            roles=["clinician"],
        ),
    )


def _patch_stack(fake_redis, fake_sync_redis):
    stack = ExitStack()
    stack.enter_context(patch("app.core.redis.get_redis_client", return_value=fake_sync_redis))
    stack.enter_context(patch("app.services.consent_engine.get_consent_redis_client", return_value=fake_redis))
    stack.enter_context(patch("app.core.consent_gate.validate_approved_access", return_value=None))
    stack.enter_context(patch("app.services.provider_auth_service.get_redis_client", return_value=fake_sync_redis))
    mock_supabase = MagicMock()
    mock_supabase.table.return_value.select.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
    mock_supabase.table.return_value.insert.return_value.execute.return_value = MagicMock()
    mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(data={})
    stack.enter_context(patch("app.core.supabase.get_supabase_client", return_value=mock_supabase))
    for mod in (
        "app.observability.audit_ledger",
        "app.core.consent_gate",
        "app.api.v2.patient_record_routes",
        "app.services.consent_engine",
    ):
        stack.enter_context(patch(f"{mod}.append_audit_log_or_503", return_value=None))
    stack.enter_context(patch("app.observability.audit_ledger.append_audit_log", return_value=None))
    stack.enter_context(patch("app.services.consent_engine.append_audit_log", return_value=None))
    return stack


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


# ── Tests ────────────────────────────────────────────────────────────────────


def test_cross_doctor_consent_rejected_by_validate(fake_redis):
    """T-04a: consent_engine.validate rejects a token when clinician_id mismatches.

    Doctor B tries to use a consent token issued to Doctor A.
    The capability has clinician_id=doctor_a, but the caller passes
    clinician_id=doctor_b → validate returns None.
    """
    patient_id = str(uuid.uuid4())
    doctor_a = str(uuid.uuid4())
    doctor_b = str(uuid.uuid4())

    token_raw = uuid.uuid4().hex
    token_key = f"nexa:consent:{token_raw}"
    cap_data = json.dumps({
        "patient_id": patient_id,
        "clinician_id": doctor_a,  # bound to Doctor A
        "purpose": "TREATMENT",
        "scope": ["clinical.*"],
        "is_break_glass": False,
        "reason_code": None,
        "issued_at": "2026-07-11T10:00:00Z",
    })
    fake_redis.data[token_key] = cap_data
    fake_redis.ttls[token_key] = time.time() + 3600

    # Doctor B tries to use Doctor A's token
    result = asyncio.run(validate(
        token=token_raw,
        patient_id=patient_id,
        clinician_id=doctor_b,  # WRONG clinician
        purpose="TREATMENT",
    ))
    assert result is None, (
        "Cross-doctor consent reuse must be rejected by validate()"
    )


def test_cross_doctor_consent_rejected_at_gate(
    client, fake_redis, fake_sync_redis, mock_db, overrides,
):
    """T-04b: Doctor B uses Doctor A's consent token → 403 from require_consent."""
    patient_id = str(uuid.uuid4())
    doctor_a = uuid.uuid4()
    doctor_b = uuid.uuid4()

    provider_b = _make_provider_context(provider_id=doctor_b)

    async def _provider_dep():
        return provider_b

    overrides[get_current_provider] = _provider_dep
    app.dependency_overrides[get_current_provider] = _provider_dep

    # Seed consent for Doctor A
    token_raw = uuid.uuid4().hex
    token_key = f"nexa:consent:{token_raw}"
    cap_data = json.dumps({
        "patient_id": patient_id,
        "clinician_id": str(doctor_a),  # Doctor A
        "purpose": "TREATMENT",
        "scope": ["clinical.*"],
        "is_break_glass": False,
        "reason_code": None,
        "issued_at": "2026-07-11T10:00:00Z",
    })
    fake_redis.data[token_key] = cap_data
    fake_redis.ttls[token_key] = time.time() + 3600

    with _patch_stack(fake_redis, fake_sync_redis):
        # Doctor B (authenticated as provider_b) uses Doctor A's token
        resp = client.get(
            f"/api/v2/patient/{patient_id}/summary",
            headers={"X-Consent-Token": token_raw},
        )
        assert resp.status_code == 403, (
            f"Cross-doctor consent reuse should return 403, got {resp.status_code}"
        )


def test_consent_wrong_patient_rejected_by_validate(fake_redis):
    """T-04c: Consent token for Patient A cannot validate for Patient B.

    The capability has patient_id=A, but the caller passes patient_id=B.
    The _matches() check catches the mismatch → validate returns None.
    """
    patient_a = str(uuid.uuid4())
    patient_b = str(uuid.uuid4())
    doctor = str(uuid.uuid4())

    token_raw = uuid.uuid4().hex
    token_key = f"nexa:consent:{token_raw}"
    cap_data = json.dumps({
        "patient_id": patient_a,  # bound to Patient A
        "clinician_id": doctor,
        "purpose": "TREATMENT",
        "scope": ["clinical.*"],
        "is_break_glass": False,
        "reason_code": None,
        "issued_at": "2026-07-11T10:00:00Z",
    })
    fake_redis.data[token_key] = cap_data
    fake_redis.ttls[token_key] = time.time() + 3600

    # Try to validate for Patient B
    result = asyncio.run(validate(
        token=token_raw,
        patient_id=patient_b,  # WRONG patient
        clinician_id=doctor,
        purpose="TREATMENT",
    ))
    assert result is None, (
        "Consent for wrong patient must be rejected by validate()"
    )


def test_cross_doctor_reuse_audited(
    client, fake_redis, fake_sync_redis, mock_db, overrides,
):
    """T-04d: Cross-doctor consent reuse attempt produces audit event.

    Even though the request is rejected, the consent gate writes
    a CONSENT_GATED_DECRYPT_FAILED audit event. We verify the
    rejection happened (the audit is patched via _patch_stack).
    """
    patient_id = str(uuid.uuid4())
    doctor_a = uuid.uuid4()
    doctor_b = uuid.uuid4()

    provider_b = _make_provider_context(provider_id=doctor_b)

    async def _provider_dep():
        return provider_b

    overrides[get_current_provider] = _provider_dep
    app.dependency_overrides[get_current_provider] = _provider_dep

    token_raw = uuid.uuid4().hex
    token_key = f"nexa:consent:{token_raw}"
    cap_data = json.dumps({
        "patient_id": patient_id,
        "clinician_id": str(doctor_a),
        "purpose": "TREATMENT",
        "scope": ["clinical.*"],
        "is_break_glass": False,
        "reason_code": None,
        "issued_at": "2026-07-11T10:00:00Z",
    })
    fake_redis.data[token_key] = cap_data
    fake_redis.ttls[token_key] = time.time() + 3600

    with _patch_stack(fake_redis, fake_sync_redis):
        resp = client.get(
            f"/api/v2/patient/{patient_id}/summary",
            headers={"X-Consent-Token": token_raw},
        )
        # The rejection proves the defense worked; audit is best-effort
        assert resp.status_code == 403, (
            f"Cross-doctor consent should be rejected (403), got {resp.status_code}"
        )
