"""Real Redis-only state-machine qualification for Phase 1B.2 remediation."""

from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import BackgroundTasks, HTTPException, Response
from redis.asyncio import Redis

import app.api.v2.consent_routes as consent_routes
import app.services.approved_access_capability as approved_access
from app.services.approved_access_capability import (
    ApprovedAccessClaimInProgress,
    issue_from_approved_request,
    validate,
)
from app.services.patient_discovery_service import (
    DiscoveryHandleInvalid,
    PatientDiscoveryService,
    _handle_key,
)

pytestmark = pytest.mark.redis


def _redis_url() -> str:
    value = os.getenv("TEST_REDIS_URL")
    if not value:
        pytest.skip("TEST_REDIS_URL is not configured")
    if "127.0.0.1" not in value:
        pytest.fail("TEST_REDIS_URL must be loopback-only")
    return value


class _DeleteFailureRedis:
    """Delegate reads/writes while making cleanup deletion unavailable."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    def __getattr__(self, name):
        return getattr(self._redis, name)

    async def delete(self, *_keys):
        raise RuntimeError("redis delete unavailable")


class _PromotionFailureRedis(_DeleteFailureRedis):
    async def eval(self, script, *args):
        if "pending_audit" in script:
            return 0
        return await self._redis.eval(script, *args)


@pytest.mark.asyncio
async def test_real_redis_audit_state_machines_without_postgres(monkeypatch) -> None:
    """Only an audited transition makes discovery or consent state actionable."""
    redis = Redis.from_url(_redis_url(), decode_responses=True)
    keys: list[str] = []
    patient = SimpleNamespace(patient_uuid=uuid4())
    service = PatientDiscoveryService(db=None, redis=redis)

    async def resolve_patient_id(_self, patient_id):
        assert patient_id == patient.patient_uuid
        return patient, False

    monkeypatch.setattr(
        PatientDiscoveryService, "resolve_patient_id", resolve_patient_id
    )

    async def issue_handle():
        handle = await service.issue_handle(
            patient=patient,
            provider_id="provider-a",
            hospital_id="hospital-a",
            session_binding="session-a",
            identifier_type="NEXA_PUBLIC_ID",
        )
        keys.append(_handle_key(handle.value))
        return handle

    async def consume_once(handle) -> bool:
        try:
            await service.consume_handle(
                raw_handle=handle.value,
                provider_id="provider-a",
                hospital_id="hospital-a",
                session_binding="session-a",
            )
            return True
        except DiscoveryHandleInvalid:
            return False

    try:
        staged = await issue_handle()
        staged_payload = json.loads(await redis.get(_handle_key(staged.value)))
        assert staged_payload["state"] == "PENDING_AUDIT"
        assert await consume_once(staged) is False

        active = await issue_handle()
        active_key = _handle_key(active.value)
        ttl_before = await redis.pttl(active_key)
        assert await service.activate_handle(raw_handle=active.value) is True
        assert await service.activate_handle(raw_handle=active.value) is False
        assert json.loads(await redis.get(active_key))["state"] == "ACTIVE"
        ttl_after = await redis.pttl(active_key)
        assert 0 < ttl_after <= ttl_before
        assert await consume_once(active) is True
        assert await consume_once(active) is False

        race = await issue_handle()
        assert await service.activate_handle(raw_handle=race.value) is True
        assert sum(await asyncio.gather(*(consume_once(race) for _ in range(4)))) == 1

        bound = await issue_handle()
        assert await service.activate_handle(raw_handle=bound.value) is True
        with pytest.raises(DiscoveryHandleInvalid):
            await service.consume_handle(
                raw_handle=bound.value,
                provider_id="provider-b",
                hospital_id="hospital-a",
                session_binding="session-a",
            )

        request_id = str(uuid4())
        consent_key = f"consent_request:{request_id}"
        keys.append(consent_key)
        await redis.set(
            consent_key,
            json.dumps({"request_id": request_id, "status": "pending_audit"}),
            ex=120,
        )
        consent_ttl_before = await redis.pttl(consent_key)
        assert await consent_routes._promote_consent_request_atomic(redis, request_id)
        promoted = json.loads(await redis.get(consent_key))
        assert promoted["status"] == "pending"
        consent_ttl_after = await redis.pttl(consent_key)
        assert 0 < consent_ttl_after <= consent_ttl_before
        assert not await consent_routes._promote_consent_request_atomic(
            redis, request_id
        )
    finally:
        if keys:
            await redis.delete(*keys)
        await redis.close(close_connection_pool=True)


@pytest.mark.asyncio
async def test_real_redis_consent_pending_audit_is_inert_after_downstream_failure(
    monkeypatch,
) -> None:
    """Redis cleanup failure never exposes an unaudited consent capability."""
    redis = Redis.from_url(_redis_url(), decode_responses=True)
    patient = SimpleNamespace(patient_uuid=uuid4())
    provider = MagicMock(
        actor_uid="provider-a",
        hospital_id="hospital-a",
        session_binding="session-a",
    )
    provider.provider.display_name = "Test clinician"
    provider.hospital.display_name = "Test hospital"
    discovery = PatientDiscoveryService(db=None, redis=redis)
    keys: list[str] = []

    async def resolve_patient_id(_self, patient_id):
        assert patient_id == patient.patient_uuid
        return patient, False

    def fake_db() -> AsyncMock:
        db = AsyncMock()
        device_result = MagicMock()
        device_result.scalar_one_or_none.return_value = MagicMock()
        no_push_result = MagicMock()
        no_push_result.scalar_one_or_none.return_value = None
        db.execute.side_effect = [device_result, no_push_result]
        return db

    async def active_handle() -> str:
        handle = await discovery.issue_handle(
            patient=patient,
            provider_id=provider.actor_uid,
            hospital_id=str(provider.hospital_id),
            session_binding=provider.session_binding,
            identifier_type="NEXA_PUBLIC_ID",
        )
        keys.append(_handle_key(handle.value))
        assert await discovery.activate_handle(raw_handle=handle.value)
        return handle.value

    monkeypatch.setattr(
        PatientDiscoveryService, "resolve_patient_id", resolve_patient_id
    )
    monkeypatch.setattr(consent_routes, "get_async_redis_client", lambda: redis)
    monkeypatch.setattr(
        consent_routes, "current_audit_context", lambda _domain: MagicMock()
    )
    try:
        delete_failure_redis = _DeleteFailureRedis(redis)
        monkeypatch.setattr(
            consent_routes, "get_redis_client", lambda: delete_failure_redis
        )
        monkeypatch.setattr(
            consent_routes,
            "append_audit_log_or_503",
            AsyncMock(side_effect=RuntimeError("audit unavailable")),
        )
        background_tasks = BackgroundTasks()
        audit_failure_handle = await active_handle()
        with pytest.raises(HTTPException) as audit_failure:
            await consent_routes.create_consent_request(
                consent_routes.ConsentChallengeRequestPayload(
                    discovery_handle=audit_failure_handle,
                    purpose="routine_checkup",
                    scope="clinical",
                ),
                background_tasks,
                provider,
                fake_db(),
            )
        assert audit_failure.value.status_code == 503
        assert not background_tasks.tasks
        consent_keys = await redis.keys("consent_request:*")
        assert len(consent_keys) == 1
        keys.extend(consent_keys)
        request_id = consent_keys[0].removeprefix("consent_request:")
        stored = json.loads(await redis.get(consent_keys[0]))
        assert stored["status"] == "pending_audit"

        monkeypatch.setattr(consent_routes, "get_redis_client", lambda: redis)
        with pytest.raises(HTTPException) as approval_error:
            await consent_routes.approve_signed_consent(
                consent_routes.SignedApprovalRequestPayload(
                    request_id=request_id,
                    patient_id=str(patient.patient_uuid),
                    decision="approved",
                    challenge_nonce=stored["challenge_nonce"],
                    device_id=str(uuid4()),
                    signature="AA==",
                ),
                str(patient.patient_uuid),
                fake_db(),
            )
        assert approval_error.value.status_code == 409
        with pytest.raises(HTTPException) as status_error:
            await consent_routes.get_consent_request_status(request_id, provider)
        assert status_error.value.status_code == 404
        with pytest.raises(HTTPException) as claim_error:
            await consent_routes.claim_approved_access(
                request_id, Response(), provider, fake_db()
            )
        assert claim_error.value.status_code == 409
        with pytest.raises(HTTPException) as replay_error:
            await consent_routes.create_consent_request(
                consent_routes.ConsentChallengeRequestPayload(
                    discovery_handle=audit_failure_handle,
                    purpose="routine_checkup",
                    scope="clinical",
                ),
                BackgroundTasks(),
                provider,
                fake_db(),
            )
        assert replay_error.value.status_code == 403

        promotion_failure_redis = _PromotionFailureRedis(redis)
        monkeypatch.setattr(
            consent_routes, "get_redis_client", lambda: promotion_failure_redis
        )
        monkeypatch.setattr(consent_routes, "append_audit_log_or_503", AsyncMock())
        promotion_tasks = BackgroundTasks()
        with pytest.raises(HTTPException) as promotion_failure:
            await consent_routes.create_consent_request(
                consent_routes.ConsentChallengeRequestPayload(
                    discovery_handle=await active_handle(),
                    purpose="routine_checkup",
                    scope="clinical",
                ),
                promotion_tasks,
                provider,
                fake_db(),
            )
        assert promotion_failure.value.status_code == 503
        assert not promotion_tasks.tasks
        promotion_keys = await redis.keys("consent_request:*")
        keys.extend(key for key in promotion_keys if key not in keys)
        promotion_states = [
            json.loads(await redis.get(key))["status"] for key in promotion_keys
        ]
        assert "pending_audit" in promotion_states
    finally:
        if keys:
            await redis.delete(*keys)
        await redis.close(close_connection_pool=True)


@pytest.mark.asyncio
async def test_real_redis_one_discovery_handle_creates_one_challenge(
    monkeypatch,
) -> None:
    redis = Redis.from_url(_redis_url(), decode_responses=True)
    patient = SimpleNamespace(patient_uuid=uuid4())
    provider = MagicMock(
        actor_uid="provider-a",
        hospital_id="hospital-a",
        session_binding="session-a",
    )
    provider.provider.display_name = "Test clinician"
    provider.hospital.display_name = "Test hospital"
    discovery = PatientDiscoveryService(db=None, redis=redis)

    async def resolve_patient_id(_self, patient_id):
        assert patient_id == patient.patient_uuid
        return patient, False

    def fake_db() -> AsyncMock:
        db = AsyncMock()
        device_result = MagicMock()
        device_result.scalar_one_or_none.return_value = MagicMock()
        no_push_result = MagicMock()
        no_push_result.scalar_one_or_none.return_value = None
        db.execute.side_effect = [device_result, no_push_result]
        return db

    monkeypatch.setattr(
        PatientDiscoveryService, "resolve_patient_id", resolve_patient_id
    )
    monkeypatch.setattr(consent_routes, "get_async_redis_client", lambda: redis)
    monkeypatch.setattr(consent_routes, "get_redis_client", lambda: redis)
    monkeypatch.setattr(
        consent_routes, "current_audit_context", lambda _domain: MagicMock()
    )
    monkeypatch.setattr(consent_routes, "append_audit_log_or_503", AsyncMock())
    handle = await discovery.issue_handle(
        patient=patient,
        provider_id=provider.actor_uid,
        hospital_id=str(provider.hospital_id),
        session_binding=provider.session_binding,
        identifier_type="NEXA_PUBLIC_ID",
    )
    assert await discovery.activate_handle(raw_handle=handle.value)

    async def create_once():
        try:
            return await consent_routes.create_consent_request(
                consent_routes.ConsentChallengeRequestPayload(
                    discovery_handle=handle.value,
                    purpose="routine_checkup",
                    scope="clinical",
                ),
                BackgroundTasks(),
                provider,
                fake_db(),
            )
        except HTTPException as exc:
            return exc

    try:
        results = await asyncio.gather(*(create_once() for _ in range(4)))
        successes = [
            result
            for result in results
            if isinstance(result, consent_routes.ConsentChallengeResponsePayload)
        ]
        failures = [result for result in results if isinstance(result, HTTPException)]
        assert len(successes) == 1
        assert len(failures) == 3
        assert all(failure.status_code == 403 for failure in failures)
        assert len(await redis.keys("consent_request:*")) == 1
    finally:
        cleanup_keys = await redis.keys("consent_request:*")
        if cleanup_keys:
            await redis.delete(*cleanup_keys)
        await redis.delete(_handle_key(handle.value))
        await redis.close(close_connection_pool=True)


@pytest.mark.asyncio
async def test_real_redis_approved_access_claim_is_exactly_once(monkeypatch) -> None:
    redis = Redis.from_url(_redis_url(), decode_responses=True)
    request = {
        "request_id": "gate-a-claim-request",
        "provider_id": "provider-a",
        "hospital_id": "hospital-a",
        "patient_id": "patient-a",
        "purpose": "treatment",
        "scope": "clinical",
        "status": "approved",
        "access_expires_at": "2099-01-01T00:00:00+00:00",
    }
    request_key = f"consent_request:{request['request_id']}"
    monkeypatch.setattr(approved_access, "get_async_redis_client", lambda: redis)
    await redis.set(request_key, json.dumps(request), ex=120)

    async def claim_once():
        try:
            return await issue_from_approved_request(request_data=request)
        except ApprovedAccessClaimInProgress as exc:
            return exc

    try:
        results = await asyncio.gather(*(claim_once() for _ in range(4)))
        successes = [result for result in results if isinstance(result, tuple)]
        failures = [
            result
            for result in results
            if isinstance(result, ApprovedAccessClaimInProgress)
        ]
        assert len(successes) == 1
        assert len(failures) == 3
        token, _capability = successes[0]
        assert (
            await validate(
                token=token,
                patient_id="patient-a",
                provider_id="provider-a",
                hospital_id="hospital-a",
                requested_category="clinical_summary",
            )
            is not None
        )
        assert (
            await validate(
                token=token,
                patient_id="patient-a",
                provider_id="provider-b",
                hospital_id="hospital-a",
                requested_category="clinical_summary",
            )
            is None
        )
    finally:
        cleanup_keys = await redis.keys("consent_access:*")
        cleanup_keys.append(request_key)
        await redis.delete(*cleanup_keys)
        await redis.close(close_connection_pool=True)
