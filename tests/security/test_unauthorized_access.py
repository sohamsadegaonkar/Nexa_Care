"""Security tests — T-06: Unauthorized Record Access.

Verifies that patient record endpoints reject:
- Requests without consent token → 403
- Cross-patient access (consent for Patient A, requesting Patient B) → 403
- Unauthenticated requests → 401 or 403

Also verifies the consent gate's validate_consent_for_patient() fails
closed when patient_id is None.

Threat model reference: docs/threat-model.md T-06
"""

from __future__ import annotations

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
from tests.conftest import DualModeTestClient, FakeRedis, FakeSyncRedis


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_provider_context(provider_id=None) -> ProviderContext:
    return ProviderContext(
        provider=ProviderIdentityContext(
            provider_id=provider_id or uuid.uuid4(),
            display_name="Dr. Access",
            contact_email="access@hospital.example",
        ),
        hospital=HospitalContext(
            hospital_id=uuid.uuid4(),
            facility_code="ACC",
            display_name="Access Hospital",
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


def test_no_consent_token_rejected(
    client, fake_redis, fake_sync_redis, mock_db, overrides,
):
    """T-06a: Authenticated doctor but no X-Consent-Token → 403.

    The require_consent gate detects the missing header and rejects.
    """
    patient_id = str(uuid.uuid4())
    provider = _make_provider_context()

    async def _provider_dep():
        return provider

    overrides[get_current_provider] = _provider_dep
    app.dependency_overrides[get_current_provider] = _provider_dep

    with _patch_stack(fake_redis, fake_sync_redis):
        resp = client.get(
            f"/api/v2/patient/{patient_id}/summary",
            # No X-Consent-Token header
        )
        assert resp.status_code == 403, (
            f"Missing consent token should return 403, got {resp.status_code}"
        )


def test_invalid_consent_token_rejected(
    client, fake_redis, fake_sync_redis, mock_db, overrides,
):
    """T-06b: Invalid/nonexistent consent token → 403.

    The token doesn't exist in Redis → validate returns None → 403.
    """
    patient_id = str(uuid.uuid4())
    provider = _make_provider_context()

    async def _provider_dep():
        return provider

    overrides[get_current_provider] = _provider_dep
    app.dependency_overrides[get_current_provider] = _provider_dep

    with _patch_stack(fake_redis, fake_sync_redis):
        resp = client.get(
            f"/api/v2/patient/{patient_id}/summary",
            headers={"X-Consent-Token": "totally-fake-token"},
        )
        assert resp.status_code == 403, (
            f"Invalid consent token should return 403, got {resp.status_code}"
        )


def test_cross_patient_consent_rejected(
    client, fake_redis, fake_sync_redis, mock_db, overrides,
):
    """T-06c: Consent for Patient A cannot access Patient B's records → 403.

    The consent capability has patient_id=A but the URL has patient_id=B.
    The consent gate checks this mismatch.
    """
    patient_a = str(uuid.uuid4())
    patient_b = str(uuid.uuid4())
    provider = _make_provider_context()
    provider_id = str(provider.provider.provider_id)

    async def _provider_dep():
        return provider

    overrides[get_current_provider] = _provider_dep
    app.dependency_overrides[get_current_provider] = _provider_dep

    # Seed consent for Patient A
    token_raw = uuid.uuid4().hex
    token_key = f"nexa:consent:{token_raw}"
    cap_data = json.dumps({
        "patient_id": patient_a,
        "clinician_id": provider_id,
        "purpose": "TREATMENT",
        "scope": ["clinical.*"],
        "is_break_glass": False,
        "reason_code": None,
        "issued_at": "2026-07-11T10:00:00Z",
    })
    fake_redis.data[token_key] = cap_data
    fake_redis.ttls[token_key] = time.time() + 3600

    with _patch_stack(fake_redis, fake_sync_redis):
        # Try to access Patient B with Patient A's consent
        resp = client.get(
            f"/api/v2/patient/{patient_b}/summary",
            headers={"X-Consent-Token": token_raw},
        )
        assert resp.status_code == 403, (
            f"Cross-patient consent should return 403, got {resp.status_code}"
        )


def test_validate_consent_for_patient_none_patient_id():
    """T-06d: validate_consent_for_patient with patient_id=None → 403.

    Server-side derived patient_id could be None if the DB entity
    doesn't exist. The consent gate must reject this.
    """
    import asyncio
    from app.core.consent_gate import validate_consent_for_patient
    from fastapi import HTTPException

    provider = _make_provider_context()

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(validate_consent_for_patient(
            patient_id=None,
            purpose="test",
            provider=provider,
            x_consent_token="some-token",
        ))
    assert exc_info.value.status_code == 403


def test_validate_consent_for_patient_missing_token():
    """T-06e: validate_consent_for_patient with no consent token → 403."""
    import asyncio
    from app.core.consent_gate import validate_consent_for_patient
    from fastapi import HTTPException

    provider = _make_provider_context()

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(validate_consent_for_patient(
            patient_id=str(uuid.uuid4()),
            purpose="test",
            provider=provider,
            x_consent_token=None,
        ))
    assert exc_info.value.status_code == 403


def test_pipeline_access_without_consent_rejected(
    client, fake_redis, fake_sync_redis, mock_db, overrides,
):
    """T-06f: Pipeline endpoint without consent → 403.

    The pipeline routes use require_consent or validate_consent_for_patient.
    Access without a valid consent token must fail closed.
    """
    job_id = str(uuid.uuid4())
    provider = _make_provider_context()

    async def _provider_dep():
        return provider

    overrides[get_current_provider] = _provider_dep
    app.dependency_overrides[get_current_provider] = _provider_dep

    with _patch_stack(fake_redis, fake_sync_redis):
        # Job status endpoint requires consent
        resp = client.get(
            f"/api/v2/pipeline/jobs/{job_id}",
            # No X-Consent-Token
        )
        assert resp.status_code == 403, (
            f"Pipeline access without consent should return 403, got {resp.status_code}"
        )
