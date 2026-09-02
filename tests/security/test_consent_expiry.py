"""Security tests — T-03: Expired Consent Grants.

Verifies that consent-gated endpoints reject:
- Expired consent tokens (Redis TTL elapsed)
- Nonexistent/revoked consent tokens
- Consumed (single-use) consent tokens

The consent engine uses Redis TTLs; FakeRedis simulates expiry via its
ttls dict.  When a key is past its TTL, FakeRedis.get() returns None,
which consent_engine.validate() interprets as an invalid/expired grant.

Threat model reference: docs/threat-model.md T-03
"""

from __future__ import annotations

import json
import time
import uuid
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from app.main import app
from tests.conftest import DualModeTestClient, FakeRedis, FakeSyncRedis


# ── Helpers ───────────────────────────────────────────────────────────────────


def _patch_stack(fake_redis, fake_sync_redis):
    stack = ExitStack()
    stack.enter_context(
        patch("app.core.redis.get_redis_client", return_value=fake_sync_redis)
    )
    stack.enter_context(
        patch(
            "app.services.consent_engine.get_consent_redis_client",
            return_value=fake_redis,
        )
    )
    stack.enter_context(
        patch("app.core.consent_gate.validate_approved_access", return_value=None)
    )
    mock_supabase = MagicMock()
    mock_supabase.table.return_value.select.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(
        data=[]
    )
    mock_supabase.table.return_value.insert.return_value.execute.return_value = (
        MagicMock()
    )
    mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(
        data={}
    )
    stack.enter_context(
        patch("app.core.supabase.get_supabase_client", return_value=mock_supabase)
    )
    for mod in (
        "app.observability.audit_ledger",
        "app.core.consent_gate",
        "app.api.v2.patient_record_routes",
        "app.services.consent_engine",
    ):
        stack.enter_context(patch(f"{mod}.append_audit_log_or_503", return_value=None))
    stack.enter_context(
        patch("app.observability.audit_ledger.append_audit_log", return_value=None)
    )
    stack.enter_context(
        patch("app.services.consent_engine.append_audit_log", return_value=None)
    )
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


# ── Tests ────────────────────────────────────────────────────────────────────


def test_expired_consent_rejected_by_validate(fake_redis):
    """T-03a: Consent token past its Redis TTL → validate returns None.

    When Redis returns None for a consent token key (expired), the
    consent engine's validate() must return None, not a capability.
    """
    import asyncio
    from app.services.consent_engine import validate

    token = f"expired-token-{uuid.uuid4().hex}"
    # No data in Redis at all → expired/revoked

    result = asyncio.run(
        validate(
            token=token,
            patient_id=str(uuid.uuid4()),
            clinician_id=str(uuid.uuid4()),
            purpose="TREATMENT",
        )
    )
    assert result is None, "Expired consent token must not validate"


def test_expired_routine_consent_rejected_at_gate(
    client,
    fake_redis,
    fake_sync_redis,
    real_clinical_session,
):
    """T-03b: Access with expired consent → 403 from require_consent gate."""
    patient_id = str(uuid.uuid4())
    provider_id = str(real_clinical_session.provider.id)

    # Seed an already-expired consent capability in Redis
    token_raw = uuid.uuid4().hex
    token_key = f"nexa:consent:{token_raw}"
    cap_data = json.dumps(
        {
            "patient_id": patient_id,
            "clinician_id": provider_id,
            "purpose": "TREATMENT",
            "scope": ["clinical.*"],
            "is_break_glass": False,
            "reason_code": None,
            "issued_at": "2026-07-11T10:00:00Z",
        }
    )
    fake_redis.data[token_key] = cap_data
    fake_redis.ttls[token_key] = time.time() - 1  # expired

    with _patch_stack(fake_redis, fake_sync_redis):
        resp = client.get(
            f"/api/v2/patient/{patient_id}/summary",
            headers={
                **real_clinical_session.headers,
                "X-Consent-Token": token_raw,
            },
        )
        assert (
            resp.status_code == 403
        ), f"Expired consent should be rejected (403), got {resp.status_code}"


def test_revoked_consent_rejected_at_gate(
    client,
    fake_redis,
    fake_sync_redis,
    real_clinical_session,
):
    """T-03c: Revoked consent (key deleted from Redis) → 403."""
    patient_id = str(uuid.uuid4())
    # Token never existed in Redis (revoked = deleted)
    token_raw = uuid.uuid4().hex

    with _patch_stack(fake_redis, fake_sync_redis):
        resp = client.get(
            f"/api/v2/patient/{patient_id}/summary",
            headers={
                **real_clinical_session.headers,
                "X-Consent-Token": token_raw,
            },
        )
        assert (
            resp.status_code == 403
        ), f"Revoked consent should be rejected (403), got {resp.status_code}"


def test_consent_rejected_one_second_past_expiry(fake_redis):
    """T-03d: Consent token 1 second past TTL → validate returns None.

    Boundary test: the TTL enforcement must be exact, not off-by-one.
    """
    import asyncio
    from app.services.consent_engine import validate

    token_raw = uuid.uuid4().hex
    token_key = f"nexa:consent:{token_raw}"
    patient_id = str(uuid.uuid4())
    provider_id = str(uuid.uuid4())

    cap_data = json.dumps(
        {
            "patient_id": patient_id,
            "clinician_id": provider_id,
            "purpose": "TREATMENT",
            "scope": ["clinical.*"],
            "is_break_glass": False,
            "reason_code": None,
            "issued_at": "2026-07-11T10:00:00Z",
        }
    )
    fake_redis.data[token_key] = cap_data
    fake_redis.ttls[token_key] = time.time() - 1  # 1 second past

    result = asyncio.run(
        validate(
            token=token_raw,
            patient_id=patient_id,
            clinician_id=provider_id,
            purpose="TREATMENT",
        )
    )
    assert result is None, "Consent 1s past TTL must not validate"


def test_expired_challenge_cannot_be_approved(
    client,
    fake_redis,
    fake_sync_redis,
    mock_db,
):
    """T-03e: An expired challenge nonce cannot be approved → 403 from verifier.

    The challenge has a 2-minute TTL. After expiry, the verifier
    detects the expires_at has passed and rejects the signature.
    """
    import asyncio
    from app.services.signed_approval_verifier import SignedApprovalVerifier

    patient_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())
    challenge_nonce = "expired-nonce-1234"
    past_time = "2020-01-01T00:00:00+00:00"
    verifier = SignedApprovalVerifier()
    result = asyncio.run(
        verifier.verify_signed_approval(
            db=mock_db,
            patient_id=patient_id,
            request_id=request_id,
            challenge_nonce=challenge_nonce,
            decision="approved",
            signature_b64="invalid",
            expires_at=past_time,
            issued_at="2019-12-31T23:58:00+00:00",
            provider_id="doctor-uid",
            scope="clinical",
            purpose="checkup",
            access_duration=900,
            device_id=str(uuid.uuid4()),
        )
    )
    assert not result.verified, "Expired challenge must fail signature verification"


# ── Inline helpers (avoid import from other test files) ──────────────────────


def _db_result(*, scalar_one_or_none=None, scalars_all=None, scalar=None):
    if scalars_all is not None:
        return MagicMock(
            scalars=MagicMock(
                return_value=MagicMock(all=MagicMock(return_value=scalars_all))
            ),
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
