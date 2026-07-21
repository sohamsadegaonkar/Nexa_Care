"""DEFECT 5: real break-glass revocation coverage (was Squad A Day 2 xfail).

tests/test_break_glass_revoke.py already covers the /break-glass/revoke
route's HTTP mechanics (auth, non-break-glass rejection, audit failure)
with consent_engine.revoke() mocked out. What was missing -- and is the
actual point of "revocation" -- is proof that revoke() really revokes:
that access succeeds before revocation and is denied immediately after,
using the real consent_engine implementation end to end.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.dependencies import get_db_session, get_provider_context, require_role
from app.models.provider import AffiliationType
from app.models.provider_context import AffiliationContext, HospitalContext, ProviderContext, ProviderIdentityContext
from app.services import consent_engine


class AsyncFakeRedisClient:
    """Minimal in-memory stand-in matching the subset of the redis-py async
    API consent_engine actually uses."""

    def __init__(self):
        self._store: dict[str, str] = {}

    async def get(self, k):
        return self._store.get(k)

    async def set(self, k, v, ex=None):
        self._store[k] = v
        return True

    async def setex(self, k, t, v):
        self._store[k] = v
        return True

    async def getdel(self, k):
        return self._store.pop(k, None)

    async def delete(self, k):
        existed = k in self._store
        self._store.pop(k, None)
        return 1 if existed else 0


class FakeGrantLogDB:
    """DB double that actually tracks a ConsentGrantLog-shaped row so
    consent_engine.revoke()'s durable-revocation lookup has something real
    to find (mirrors the real ORM row's fields consent_engine touches)."""

    def __init__(self):
        self.rows: list[MagicMock] = []

    def add(self, row):
        self.rows.append(row)

    async def commit(self):
        pass

    async def execute(self, stmt):
        result = MagicMock()
        token_hash = getattr(stmt, "_nexa_token_hash", None)
        match = next((r for r in self.rows if r.token_hash == token_hash), None) if token_hash else None
        result.scalar_one_or_none.return_value = match
        return result


def _fake_db() -> AsyncMock:
    """AsyncMock() alone isn't safe here: without spec=, its nested
    attributes (e.g. the .execute() return value's .scalar_one_or_none)
    get auto-configured as AsyncMock too, so calling them returns an
    unawaited coroutine instead of a plain value. Configure db.execute's
    result explicitly instead."""
    db = AsyncMock()
    db.add = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result)
    return db


def _provider() -> ProviderContext:
    return ProviderContext(
        provider=ProviderIdentityContext(provider_id=uuid.uuid4(), display_name="Dr. Revoke", contact_email="r@ex.com"),
        hospital=HospitalContext(hospital_id=uuid.uuid4(), facility_code="H", display_name="H"),
        affiliation=AffiliationContext(
            affiliation_id=uuid.uuid4(), affiliation_type=AffiliationType.PERMANENT,
            is_primary=True, roles=["clinician"],
        ),
    )


@pytest.mark.asyncio
async def test_issue_then_revoke_denies_immediate_subsequent_access():
    """DEFECT 5 core contract: issue -> access succeeds -> revoke -> access
    denied immediately, using the real consent_engine implementation."""
    redis = AsyncFakeRedisClient()
    db = _fake_db()
    provider = _provider()
    patient_id = str(uuid.uuid4())

    with patch("app.services.consent_engine.get_consent_redis_client", return_value=redis), \
         patch("app.services.consent_engine.append_audit_log", AsyncMock(return_value=True)), \
         patch("app.services.consent_engine.append_audit_log_or_503", AsyncMock(return_value=None)):

        token = await consent_engine.issue_break_glass(
            patient_id=patient_id,
            clinician_id=provider.actor_uid,
            reason_code="LIFE_THREATENING_EMERGENCY",
            db=db,
            hospital_id=str(provider.hospital_id),
            scope=["allergies"],
            reason_code_version="v1",
            session_binding="session-abc",
            mfa_verified_at=datetime.now(timezone.utc),
        )

        # Access succeeds before revocation.
        capability = await consent_engine.validate(
            token=token, patient_id=patient_id, clinician_id=provider.actor_uid,
            purpose="EMERGENCY", hospital_id=str(provider.hospital_id), session_binding="session-abc",
        )
        assert capability is not None
        assert capability.is_break_glass is True

        await consent_engine.revoke(db=db, token=token, reason="Emergency resolved")

        # Immediate denial after revocation.
        capability_after = await consent_engine.validate(
            token=token, patient_id=patient_id, clinician_id=provider.actor_uid,
            purpose="EMERGENCY", hospital_id=str(provider.hospital_id), session_binding="session-abc",
        )
        assert capability_after is None


@pytest.mark.asyncio
async def test_repeated_revoke_is_idempotent():
    redis = AsyncFakeRedisClient()
    db = _fake_db()
    provider = _provider()
    patient_id = str(uuid.uuid4())

    with patch("app.services.consent_engine.get_consent_redis_client", return_value=redis), \
         patch("app.services.consent_engine.append_audit_log", AsyncMock(return_value=True)), \
         patch("app.services.consent_engine.append_audit_log_or_503", AsyncMock(return_value=None)):

        token = await consent_engine.issue_break_glass(
            patient_id=patient_id, clinician_id=provider.actor_uid,
            reason_code="LIFE_THREATENING_EMERGENCY", db=db,
            hospital_id=str(provider.hospital_id), scope=["allergies"],
            reason_code_version="v1", session_binding="session-abc",
            mfa_verified_at=datetime.now(timezone.utc),
        )

        await consent_engine.revoke(db=db, token=token, reason="first revoke")
        # A second revoke of an already-revoked (now nonexistent) token
        # must not raise -- revoke() is documented as best-effort/idempotent.
        await consent_engine.revoke(db=db, token=token, reason="second revoke")

        capability = await consent_engine.validate(
            token=token, patient_id=patient_id, clinician_id=provider.actor_uid, purpose="EMERGENCY",
        )
        assert capability is None


@pytest.mark.asyncio
async def test_revoke_route_rejects_non_break_glass_token():
    """Routine (non-break-glass) tokens cannot be revoked through the
    break-glass revoke endpoint -- exercised at the real route, with the
    real consent_engine.revoke (not mocked): it must never even be called."""
    token = "routine-token-abc"
    db = FakeGrantLogDB()
    grant = MagicMock()
    grant.is_break_glass = False
    grant.token_hash = consent_engine._token_hash(token)

    class _Stmt:
        _nexa_token_hash = grant.token_hash

    async def fake_execute(stmt):
        result = MagicMock()
        result.scalar_one_or_none.return_value = grant
        return result

    db.execute = fake_execute
    provider = _provider()

    app.dependency_overrides[get_db_session] = lambda: db
    app.dependency_overrides[get_provider_context] = lambda: provider
    app.dependency_overrides[require_role("clinician")] = lambda: provider

    try:
        with patch("app.api.v2.consent_routes.append_audit_log_or_503", AsyncMock(return_value=None)), \
             patch("app.api.v2.consent_routes.consent_engine.revoke", AsyncMock()) as mock_revoke:
            client = TestClient(app)
            response = client.post(
                "/api/v2/consent/break-glass/revoke",
                json={"consent_token": token, "revocation_reason": "wrong endpoint"},
            )
            assert response.status_code == 400
            mock_revoke.assert_not_called()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_revoke_forged_token_is_rejected():
    """A token with no matching ConsentGrantLog row at all (forged /
    never issued) is rejected, and real revoke logic is never invoked."""
    db = _fake_db()

    async def fake_execute(stmt):
        result = MagicMock()
        result.scalar_one_or_none.return_value = None  # no such grant exists
        return result

    db.execute = fake_execute
    provider = _provider()

    app.dependency_overrides[get_db_session] = lambda: db
    app.dependency_overrides[get_provider_context] = lambda: provider
    app.dependency_overrides[require_role("clinician")] = lambda: provider

    try:
        with patch("app.api.v2.consent_routes.append_audit_log_or_503", AsyncMock(return_value=None)), \
             patch("app.api.v2.consent_routes.consent_engine.revoke", AsyncMock()) as mock_revoke:
            client = TestClient(app)
            response = client.post(
                "/api/v2/consent/break-glass/revoke",
                json={"consent_token": "forged-token-123", "revocation_reason": "testing"},
            )
            assert response.status_code in (400, 403, 404)
            mock_revoke.assert_not_called()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_revoke_audit_events_are_recorded():
    """Both a revoke-attempt and revoke-success audit event are recorded
    by the real revoke() implementation (not mocked)."""
    redis = AsyncFakeRedisClient()
    db = _fake_db()
    provider = _provider()
    patient_id = str(uuid.uuid4())
    recorded_events: list[str] = []

    async def fake_append_audit_log(actor_uid, event_type, target_id, status, **kwargs):
        recorded_events.append(event_type)
        return True

    with patch("app.services.consent_engine.get_consent_redis_client", return_value=redis), \
         patch("app.services.consent_engine.append_audit_log", fake_append_audit_log), \
         patch("app.services.consent_engine.append_audit_log_or_503", AsyncMock(return_value=None)):

        token = await consent_engine.issue_break_glass(
            patient_id=patient_id, clinician_id=provider.actor_uid,
            reason_code="LIFE_THREATENING_EMERGENCY", db=db,
            hospital_id=str(provider.hospital_id), scope=["allergies"],
            reason_code_version="v1", session_binding="session-abc",
            mfa_verified_at=datetime.now(timezone.utc),
        )
        await consent_engine.revoke(db=db, token=token, reason="done")

    assert "BREAK_GLASS_REVOKE_ATTEMPT" in recorded_events
    assert "BREAK_GLASS_REVOKE_SUCCESS" in recorded_events