"""Route-level non-regression coverage for Phase 1B.2 remediation."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException, Response
from fastapi.testclient import TestClient

import app.api.v2.consent_routes as consent_routes

from app.core.database import get_db_session
from app.core.dependencies import get_provider_context, require_clinical_capability
from app.main import app
from app.models.provider import AffiliationType
from app.models.provider_context import (
    AffiliationContext,
    HospitalContext,
    ProviderContext,
    ProviderIdentityContext,
)
from app.security.provider_capabilities import ClinicalCapability
from app.services.patient_discovery_service import (
    DiscoveryHandle,
    DiscoveryHandleInvalid,
    PatientDiscoveryService,
    _handle_key,
)


class _Redis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key, value, **_kwargs):
        self.store[key] = value
        return True

    async def delete(self, key):
        return self.store.pop(key, None) is not None

    async def get(self, key):
        return self.store.get(key)


class _StateMachineRedis(_Redis):
    """Minimal Redis Lua model used to prove fail-closed route ordering."""

    async def eval(self, script, _key_count, key):
        raw = await self.get(key)
        if raw is None:
            return False
        payload = json.loads(raw)
        if "data.state ~= 'ACTIVE'" in script:
            if payload.get("state") != "ACTIVE":
                return False
            self.store.pop(key)
            return raw
        if "data.state ~= 'PENDING_AUDIT'" in script:
            if payload.get("state") != "PENDING_AUDIT":
                return 0
            payload["state"] = "ACTIVE"
            self.store[key] = json.dumps(payload)
            return 1
        raise AssertionError("Unexpected Lua script")


@pytest.fixture
def provider() -> ProviderContext:
    return ProviderContext(
        provider=ProviderIdentityContext(
            provider_id=uuid4(),
            display_name="Test clinician",
            contact_email="test@example.invalid",
        ),
        hospital=HospitalContext(
            hospital_id=uuid4(), facility_code="TEST", display_name="Test Hospital"
        ),
        affiliation=AffiliationContext(
            affiliation_id=uuid4(),
            affiliation_type=AffiliationType.PERMANENT,
            is_primary=True,
            roles=["clinician"],
        ),
        session_binding="test-session",
    )


@pytest.fixture
def client(provider):
    def provider_factory():
        return provider

    discover = require_clinical_capability(ClinicalCapability.PATIENT_DISCOVER)
    consent_request = require_clinical_capability(ClinicalCapability.CONSENT_REQUEST)
    app.dependency_overrides[discover] = provider_factory
    app.dependency_overrides[consent_request] = provider_factory
    app.dependency_overrides[get_provider_context] = provider_factory
    app.dependency_overrides[get_db_session] = lambda: AsyncMock()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("path", "payload", "seam"),
    [
        (
            "/api/v2/consent/grant",
            {"patient_id": str(uuid4()), "scope": ["clinical"]},
            "app.api.v2.consent_routes.consent_engine.issue",
        ),
        (
            "/api/v2/consent/routine/issue",
            {"patient_id": str(uuid4()), "scope": ["clinical"]},
            None,
        ),
    ],
)
def test_authenticated_direct_routine_issuers_are_retired(client, path, payload, seam):
    if seam is None:
        response = client.post(path, json=payload)
    else:
        with patch(seam, new_callable=AsyncMock) as issue:
            response = client.post(path, json=payload)
        issue.assert_not_awaited()
    assert response.status_code == 410
    assert response.json()["detail"]["error_code"] == "ROUTINE_DIRECT_ISSUANCE_RETIRED"
    if seam is None:
        import app.api.v2.consent_routes as consent_routes

        assert not hasattr(consent_routes, "issue_routine")


def test_nfc_success_audits_before_disclosing_only_a_handle(client, provider):
    patient = SimpleNamespace(patient_uuid=uuid4())
    handle = DiscoveryHandle("opaque-handle", datetime.now(timezone.utc))
    with (
        patch("app.api.v2.nfc_routes.get_async_redis_client", return_value=_Redis()),
        patch(
            "app.api.v2.nfc_routes.atomic_fixed_window",
            new=AsyncMock(return_value=(1, 60)),
        ),
        patch("app.api.v2.nfc_routes.current_audit_context", return_value=MagicMock()),
        patch(
            "app.api.v2.nfc_routes.CardResolutionService.resolve_card",
            new=AsyncMock(return_value=patient.patient_uuid),
        ),
        patch(
            "app.api.v2.nfc_routes.PatientDiscoveryService.resolve_patient_id",
            new=AsyncMock(return_value=(patient, False)),
        ),
        patch(
            "app.api.v2.nfc_routes.PatientDiscoveryService.issue_handle",
            new=AsyncMock(return_value=handle),
        ),
        patch(
            "app.api.v2.nfc_routes.PatientDiscoveryService.activate_handle",
            new=AsyncMock(return_value=True),
        ) as activate,
        patch(
            "app.api.v2.nfc_routes.append_audit_log_or_503", new=AsyncMock()
        ) as audit,
    ):
        response = client.post("/api/v2/nfc/resolve", json={"card_uid": "A1"})
    assert response.status_code == 200, response.text
    assert response.json()["discovery_handle"] == handle.value
    assert str(patient.patient_uuid) not in response.text
    assert audit.await_count == 1
    activate.assert_awaited_once_with(raw_handle=handle.value)


def test_nfc_flood_limit_is_provider_account_scoped(client, provider):
    limiter = AsyncMock(return_value=(31, 60))
    with (
        patch("app.api.v2.nfc_routes.get_async_redis_client", return_value=_Redis()),
        patch("app.api.v2.nfc_routes.atomic_fixed_window", new=limiter),
        patch("app.api.v2.nfc_routes.current_audit_context", return_value=MagicMock()),
        patch("app.api.v2.nfc_routes.append_audit_log_or_503", new=AsyncMock()),
        patch(
            "app.api.v2.nfc_routes.CardResolutionService.resolve_card",
            new=AsyncMock(),
        ) as resolve,
    ):
        response = client.post("/api/v2/nfc/resolve", json={"card_uid": "A1"})
    assert response.status_code == 429
    limiter.assert_awaited_once_with(
        ANY,
        f"nfc_resolve_rate:{provider.actor_uid}",
        60,
    )
    resolve.assert_not_awaited()


def test_nfc_activation_failure_after_audit_discloses_nothing(client):
    patient = SimpleNamespace(patient_uuid=uuid4())
    handle = DiscoveryHandle("opaque-handle", datetime.now(timezone.utc))
    with (
        patch("app.api.v2.nfc_routes.get_async_redis_client", return_value=_Redis()),
        patch(
            "app.api.v2.nfc_routes.atomic_fixed_window",
            new=AsyncMock(return_value=(1, 60)),
        ),
        patch("app.api.v2.nfc_routes.current_audit_context", return_value=MagicMock()),
        patch(
            "app.api.v2.nfc_routes.CardResolutionService.resolve_card",
            new=AsyncMock(return_value=patient.patient_uuid),
        ),
        patch(
            "app.api.v2.nfc_routes.PatientDiscoveryService.resolve_patient_id",
            new=AsyncMock(return_value=(patient, False)),
        ),
        patch(
            "app.api.v2.nfc_routes.PatientDiscoveryService.issue_handle",
            new=AsyncMock(return_value=handle),
        ),
        patch(
            "app.api.v2.nfc_routes.PatientDiscoveryService.activate_handle",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "app.api.v2.nfc_routes.PatientDiscoveryService.revoke_handle",
            new=AsyncMock(),
        ),
        patch("app.api.v2.nfc_routes.append_audit_log_or_503", new=AsyncMock()),
    ):
        response = client.post("/api/v2/nfc/resolve", json={"card_uid": "A1"})
    assert response.status_code == 503
    assert handle.value not in response.text
    assert str(patient.patient_uuid) not in response.text


def test_nfc_audit_failure_revokes_staged_handle_and_discloses_nothing(client):
    patient = SimpleNamespace(patient_uuid=uuid4())
    handle = DiscoveryHandle("opaque-handle", datetime.now(timezone.utc))
    with (
        patch("app.api.v2.nfc_routes.get_async_redis_client", return_value=_Redis()),
        patch(
            "app.api.v2.nfc_routes.atomic_fixed_window",
            new=AsyncMock(return_value=(1, 60)),
        ),
        patch("app.api.v2.nfc_routes.current_audit_context", return_value=MagicMock()),
        patch(
            "app.api.v2.nfc_routes.CardResolutionService.resolve_card",
            new=AsyncMock(return_value=patient.patient_uuid),
        ),
        patch(
            "app.api.v2.nfc_routes.PatientDiscoveryService.resolve_patient_id",
            new=AsyncMock(return_value=(patient, False)),
        ),
        patch(
            "app.api.v2.nfc_routes.PatientDiscoveryService.issue_handle",
            new=AsyncMock(return_value=handle),
        ),
        patch(
            "app.api.v2.nfc_routes.PatientDiscoveryService.revoke_handle",
            new=AsyncMock(),
        ) as revoke,
        patch(
            "app.api.v2.nfc_routes.append_audit_log_or_503",
            new=AsyncMock(side_effect=RuntimeError("audit unavailable")),
        ),
    ):
        response = client.post("/api/v2/nfc/resolve", json={"card_uid": "A1"})
    assert response.status_code == 503
    assert handle.value not in response.text
    assert str(patient.patient_uuid) not in response.text
    revoke.assert_awaited_once_with(raw_handle=handle.value)


def test_discovery_audit_failure_revokes_staged_handle_and_discloses_nothing(
    client, provider
):
    patient = SimpleNamespace(patient_uuid=uuid4())
    handle = DiscoveryHandle("opaque-handle", datetime.now(timezone.utc))
    with (
        patch(
            "app.api.v2.patient_discovery_routes.get_async_redis_client",
            return_value=_Redis(),
        ),
        patch(
            "app.api.v2.patient_discovery_routes.atomic_fixed_window",
            new=AsyncMock(return_value=(1, 60)),
        ),
        patch(
            "app.api.v2.patient_discovery_routes.current_audit_context",
            return_value=MagicMock(),
        ),
        patch(
            "app.api.v2.patient_discovery_routes.PatientDiscoveryService.resolve_public_id",
            new=AsyncMock(return_value=(patient, False)),
        ),
        patch(
            "app.api.v2.patient_discovery_routes.PatientDiscoveryService.issue_handle",
            new=AsyncMock(return_value=handle),
        ),
        patch(
            "app.api.v2.patient_discovery_routes.PatientDiscoveryService.revoke_handle",
            new=AsyncMock(),
        ) as revoke,
        patch(
            "app.api.v2.patient_discovery_routes.append_audit_log_or_503",
            new=AsyncMock(side_effect=[None, RuntimeError("audit unavailable")]),
        ),
    ):
        response = client.post(
            "/api/v2/patient-discovery",
            json={"identifier_type": "NEXA_PUBLIC_ID", "value": "NC-" + "A" * 24},
        )
    assert response.status_code == 503
    assert handle.value not in response.text
    assert str(patient.patient_uuid) not in response.text
    revoke.assert_awaited_once_with(raw_handle=handle.value)


def test_discovery_activation_failure_after_audit_discloses_nothing(client):
    patient = SimpleNamespace(patient_uuid=uuid4())
    handle = DiscoveryHandle("opaque-handle", datetime.now(timezone.utc))
    with (
        patch(
            "app.api.v2.patient_discovery_routes.get_async_redis_client",
            return_value=_Redis(),
        ),
        patch(
            "app.api.v2.patient_discovery_routes.atomic_fixed_window",
            new=AsyncMock(return_value=(1, 60)),
        ),
        patch(
            "app.api.v2.patient_discovery_routes.current_audit_context",
            return_value=MagicMock(),
        ),
        patch(
            "app.api.v2.patient_discovery_routes.PatientDiscoveryService.resolve_public_id",
            new=AsyncMock(return_value=(patient, False)),
        ),
        patch(
            "app.api.v2.patient_discovery_routes.PatientDiscoveryService.issue_handle",
            new=AsyncMock(return_value=handle),
        ),
        patch(
            "app.api.v2.patient_discovery_routes.PatientDiscoveryService.activate_handle",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "app.api.v2.patient_discovery_routes.PatientDiscoveryService.revoke_handle",
            new=AsyncMock(),
        ),
        patch(
            "app.api.v2.patient_discovery_routes.append_audit_log_or_503",
            new=AsyncMock(),
        ),
    ):
        response = client.post(
            "/api/v2/patient-discovery",
            json={"identifier_type": "NEXA_PUBLIC_ID", "value": "NC-" + "A" * 24},
        )
    assert response.status_code == 503
    assert handle.value not in response.text
    assert str(patient.patient_uuid) not in response.text


def test_discovery_audit_delete_failure_leaves_handle_inert(client, provider):
    patient = SimpleNamespace(patient_uuid=uuid4())
    redis = _StateMachineRedis()
    redis.delete = AsyncMock(side_effect=RuntimeError("redis delete unavailable"))
    raw_handle = "known-discovery-handle"
    with (
        patch(
            "app.api.v2.patient_discovery_routes.get_async_redis_client",
            return_value=redis,
        ),
        patch(
            "app.api.v2.patient_discovery_routes.atomic_fixed_window",
            new=AsyncMock(return_value=(1, 60)),
        ),
        patch(
            "app.api.v2.patient_discovery_routes.current_audit_context",
            return_value=MagicMock(),
        ),
        patch(
            "app.api.v2.patient_discovery_routes.PatientDiscoveryService.resolve_public_id",
            new=AsyncMock(return_value=(patient, False)),
        ),
        patch(
            "app.services.patient_discovery_service.secrets.token_urlsafe",
            return_value=raw_handle,
        ),
        patch(
            "app.api.v2.patient_discovery_routes.append_audit_log_or_503",
            new=AsyncMock(side_effect=[None, RuntimeError("audit unavailable")]),
        ),
    ):
        response = client.post(
            "/api/v2/patient-discovery",
            json={"identifier_type": "NEXA_PUBLIC_ID", "value": "NC-" + "A" * 24},
        )
    assert response.status_code == 503
    assert raw_handle not in response.text
    stored = json.loads(redis.store[_handle_key(raw_handle)])
    assert stored["state"] == "PENDING_AUDIT"
    with pytest.raises(DiscoveryHandleInvalid):
        asyncio.run(
            PatientDiscoveryService(db=None, redis=redis).consume_handle(
                raw_handle=raw_handle,
                provider_id=provider.actor_uid,
                hospital_id=str(provider.hospital_id),
                session_binding=provider.session_binding,
            )
        )


def test_nfc_audit_delete_failure_leaves_handle_inert(client, provider):
    patient = SimpleNamespace(patient_uuid=uuid4())
    redis = _StateMachineRedis()
    redis.delete = AsyncMock(side_effect=RuntimeError("redis delete unavailable"))
    raw_handle = "known-nfc-handle"
    with (
        patch("app.api.v2.nfc_routes.get_async_redis_client", return_value=redis),
        patch(
            "app.api.v2.nfc_routes.atomic_fixed_window",
            new=AsyncMock(return_value=(1, 60)),
        ),
        patch("app.api.v2.nfc_routes.current_audit_context", return_value=MagicMock()),
        patch(
            "app.api.v2.nfc_routes.CardResolutionService.resolve_card",
            new=AsyncMock(return_value=patient.patient_uuid),
        ),
        patch(
            "app.api.v2.nfc_routes.PatientDiscoveryService.resolve_patient_id",
            new=AsyncMock(return_value=(patient, False)),
        ),
        patch(
            "app.services.patient_discovery_service.secrets.token_urlsafe",
            return_value=raw_handle,
        ),
        patch(
            "app.api.v2.nfc_routes.append_audit_log_or_503",
            new=AsyncMock(side_effect=RuntimeError("audit unavailable")),
        ),
    ):
        response = client.post("/api/v2/nfc/resolve", json={"card_uid": "A1"})
    assert response.status_code == 503
    assert raw_handle not in response.text
    stored = json.loads(redis.store[_handle_key(raw_handle)])
    assert stored["state"] == "PENDING_AUDIT"
    with pytest.raises(DiscoveryHandleInvalid):
        asyncio.run(
            PatientDiscoveryService(db=None, redis=redis).consume_handle(
                raw_handle=raw_handle,
                provider_id=provider.actor_uid,
                hospital_id=str(provider.hospital_id),
                session_binding=provider.session_binding,
            )
        )


def test_consent_audit_failure_invalidates_challenge_without_reusing_handle(client):
    patient = SimpleNamespace(patient_uuid=uuid4())
    redis = _Redis()
    db = AsyncMock()
    app.dependency_overrides[get_db_session] = lambda: db
    device_result = MagicMock()
    device_result.scalar_one_or_none.return_value = MagicMock()
    no_push_result = MagicMock()
    no_push_result.scalar_one_or_none.return_value = None
    with (
        patch("app.api.v2.consent_routes.get_async_redis_client", return_value=redis),
        patch("app.api.v2.consent_routes.get_redis_client", return_value=redis),
        patch(
            "app.api.v2.consent_routes.current_audit_context", return_value=MagicMock()
        ),
        patch(
            "app.services.patient_discovery_service.PatientDiscoveryService.consume_handle",
            new=AsyncMock(side_effect=[patient, DiscoveryHandleInvalid()]),
        ) as consume,
        patch(
            "app.api.v2.consent_routes.append_audit_log_or_503",
            new=AsyncMock(side_effect=RuntimeError("audit unavailable")),
        ),
    ):
        db.execute.side_effect = [device_result, no_push_result]
        response = client.post(
            "/api/v2/consent/request",
            json={
                "discovery_handle": "h" * 32,
                "purpose": "routine_checkup",
                "scope": "clinical",
            },
        )
        replay = client.post(
            "/api/v2/consent/request",
            json={
                "discovery_handle": "h" * 32,
                "purpose": "routine_checkup",
                "scope": "clinical",
            },
        )
    assert response.status_code == 503
    assert (
        response.json()["detail"]["error_code"] == "CONSENT_SECURITY_AUDIT_UNAVAILABLE"
    )
    assert not [key for key in redis.store if key.startswith("consent_request:")]
    assert replay.status_code == 403
    assert consume.await_count == 2


def test_consent_audit_failure_with_delete_failure_leaves_inert_challenge(
    client, provider
):
    """Audit durability, not cleanup success, controls challenge usability."""
    patient = SimpleNamespace(patient_uuid=uuid4())
    redis = _Redis()
    redis.delete = AsyncMock(side_effect=RuntimeError("redis delete unavailable"))
    db = AsyncMock()
    app.dependency_overrides[get_db_session] = lambda: db
    device_result = MagicMock()
    device_result.scalar_one_or_none.return_value = MagicMock()
    no_push_result = MagicMock()
    no_push_result.scalar_one_or_none.return_value = None
    with (
        patch("app.api.v2.consent_routes.get_async_redis_client", return_value=redis),
        patch("app.api.v2.consent_routes.get_redis_client", return_value=redis),
        patch(
            "app.api.v2.consent_routes.current_audit_context", return_value=MagicMock()
        ),
        patch(
            "app.services.patient_discovery_service.PatientDiscoveryService.consume_handle",
            new=AsyncMock(side_effect=[patient, DiscoveryHandleInvalid()]),
        ) as consume,
        patch(
            "app.api.v2.consent_routes.append_audit_log_or_503",
            new=AsyncMock(side_effect=RuntimeError("audit unavailable")),
        ),
    ):
        db.execute.side_effect = [device_result, no_push_result]
        response = client.post(
            "/api/v2/consent/request",
            json={
                "discovery_handle": "h" * 32,
                "purpose": "routine_checkup",
                "scope": "clinical",
            },
        )
        stored = [
            (key, json.loads(value))
            for key, value in redis.store.items()
            if key.startswith("consent_request:")
        ]
        assert response.status_code == 503
        assert len(stored) == 1
        request_key, challenge = stored[0]
        assert challenge["status"] == "pending_audit"
        request_id = request_key.removeprefix("consent_request:")
        with pytest.raises(HTTPException) as status_error:
            asyncio.run(consent_routes.get_consent_request_status(request_id, provider))
        assert status_error.value.status_code == 404
        with pytest.raises(HTTPException) as claim_error:
            asyncio.run(
                consent_routes.claim_approved_access(
                    request_id, Response(), provider, db
                )
            )
        assert claim_error.value.status_code == 409
        signed_approval = consent_routes.SignedApprovalRequestPayload(
            request_id=request_id,
            patient_id=str(patient.patient_uuid),
            decision="approved",
            challenge_nonce=challenge["challenge_nonce"],
            device_id=str(uuid4()),
            signature="AA==",
        )
        with pytest.raises(HTTPException) as approval_error:
            asyncio.run(
                consent_routes.approve_signed_consent(
                    signed_approval, str(patient.patient_uuid), db
                )
            )
        assert approval_error.value.status_code == 409
        replay = client.post(
            "/api/v2/consent/request",
            json={
                "discovery_handle": "h" * 32,
                "purpose": "routine_checkup",
                "scope": "clinical",
            },
        )
    assert replay.status_code == 403
    assert consume.await_count == 2
