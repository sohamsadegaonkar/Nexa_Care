from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException, Response

from app.api.v2.consent_routes import claim_approved_access
from app.models.provider import AffiliationType
from app.models.provider_context import (
    AffiliationContext, HospitalContext, ProviderContext, ProviderIdentityContext,
)

from app.services.approved_access_capability import (
    CAPABILITY_PREFIX,
    invalidate_request,
    issue_from_approved_request,
    token_hash,
    validate,
)


class MemoryRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str):
        return self.data.get(key)

    async def set(self, key: str, value: str, **kwargs):
        if kwargs.get("nx") and key in self.data:
            return False
        self.data[key] = value
        return True

    async def delete(self, key: str):
        self.data.pop(key, None)
        return 1

    async def eval(self, _script: str, _numkeys: int, key: str, value: str):
        if self.data.get(key) == value:
            self.data.pop(key, None)
            return 1
        return 0


def approved_request(**overrides) -> dict:
    value = {
        "request_id": "request-1",
        "provider_id": "provider-1",
        "hospital_id": "hospital-1",
        "patient_id": "patient-1",
        "purpose": "treatment",
        "scope": "clinical",
        "status": "approved",
        "access_expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
    }
    value.update(overrides)
    return value


def seed_request(redis: MemoryRedis, request: dict) -> None:
    redis.data[f"consent_request:{request['request_id']}"] = json.dumps(request)


def provider_context(provider_id: str = "00000000-0000-0000-0000-000000000001",
                     hospital_id: str = "00000000-0000-0000-0000-000000000002") -> ProviderContext:
    return ProviderContext(
        provider=ProviderIdentityContext(
            provider_id=uuid.UUID(provider_id), display_name="Provider", contact_email="provider@example.test",
        ),
        hospital=HospitalContext(
            hospital_id=uuid.UUID(hospital_id), facility_code="ALPHA", display_name="Alpha Hospital",
        ),
        affiliation=AffiliationContext(
            affiliation_id=uuid.uuid4(), affiliation_type=AffiliationType.PERMANENT,
            roles=["clinician"], is_primary=True,
        ),
    )


@pytest.mark.asyncio
async def test_claim_stores_only_hash_and_rotation_invalidates_prior_token():
    redis = MemoryRedis()
    request = approved_request()
    seed_request(redis, request)
    with patch("app.services.approved_access_capability.get_async_redis_client", return_value=redis):
        first, _ = await issue_from_approved_request(request_data=request)
        second, _ = await issue_from_approved_request(request_data=request)

        assert first != second
        assert first not in " ".join(redis.data.keys())
        assert first not in " ".join(redis.data.values())
        assert f"{CAPABILITY_PREFIX}{token_hash(first)}" not in redis.data
        assert f"{CAPABILITY_PREFIX}{token_hash(second)}" in redis.data
        assert await validate(
            token=first, patient_id="patient-1", provider_id="provider-1",
            hospital_id="hospital-1", requested_category="clinical_summary",
        ) is None


@pytest.mark.asyncio
async def test_capability_is_bound_to_provider_hospital_patient_and_scope():
    redis = MemoryRedis()
    request = approved_request()
    seed_request(redis, request)
    with patch("app.services.approved_access_capability.get_async_redis_client", return_value=redis):
        token, capability = await issue_from_approved_request(request_data=request)
        assert capability.request_id == "request-1"
        assert await validate(
            token=token, patient_id="patient-1", provider_id="provider-1",
            hospital_id="hospital-1", requested_category="timeline_view",
        ) is not None
        for kwargs in (
            {"patient_id": "patient-2", "provider_id": "provider-1", "hospital_id": "hospital-1", "requested_category": "clinical_summary"},
            {"patient_id": "patient-1", "provider_id": "provider-2", "hospital_id": "hospital-1", "requested_category": "clinical_summary"},
            {"patient_id": "patient-1", "provider_id": "provider-1", "hospital_id": "hospital-2", "requested_category": "clinical_summary"},
            {"patient_id": "patient-1", "provider_id": "provider-1", "hospital_id": "hospital-1", "requested_category": "full"},
        ):
            assert await validate(token=token, **kwargs) is None


@pytest.mark.asyncio
async def test_expired_or_invalidated_request_fails_closed():
    redis = MemoryRedis()
    request = approved_request()
    seed_request(redis, request)
    with patch("app.services.approved_access_capability.get_async_redis_client", return_value=redis):
        token, _ = await issue_from_approved_request(request_data=request)
        request["status"] = "cancelled"
        seed_request(redis, request)
        assert await validate(
            token=token, patient_id="patient-1", provider_id="provider-1",
            hospital_id="hospital-1", requested_category="clinical_summary",
        ) is None
        await invalidate_request("request-1")
        assert f"{CAPABILITY_PREFIX}{token_hash(token)}" not in redis.data


@pytest.mark.asyncio
@pytest.mark.parametrize("request_status", ["pending", "denied", "cancelled"])
async def test_non_approved_request_cannot_be_claimed(request_status: str):
    redis = MemoryRedis()
    provider = provider_context()
    request = approved_request(
        provider_id=provider.actor_uid,
        hospital_id=str(provider.hospital_id),
        status=request_status,
    )
    seed_request(redis, request)
    with patch("app.api.v2.consent_routes.get_redis_client", return_value=redis):
        with pytest.raises(HTTPException) as exc:
            await claim_approved_access("request-1", Response(), provider, MagicMock())
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_foreign_provider_or_hospital_cannot_claim():
    redis = MemoryRedis()
    owner = provider_context()
    request = approved_request(provider_id=owner.actor_uid, hospital_id=str(owner.hospital_id))
    seed_request(redis, request)
    foreign_provider = provider_context("00000000-0000-0000-0000-000000000003")
    foreign_hospital = provider_context(hospital_id="00000000-0000-0000-0000-000000000004")
    with patch("app.api.v2.consent_routes.get_redis_client", return_value=redis):
        for provider in (foreign_provider, foreign_hospital):
            with pytest.raises(HTTPException) as exc:
                await claim_approved_access("request-1", Response(), provider, MagicMock())
            assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_missing_or_expired_request_cannot_be_claimed():
    redis = MemoryRedis()
    provider = provider_context()
    with patch("app.api.v2.consent_routes.get_redis_client", return_value=redis):
        with pytest.raises(HTTPException) as missing:
            await claim_approved_access("missing", Response(), provider, MagicMock())
        assert missing.value.status_code == 404

        request = approved_request(
            provider_id=provider.actor_uid,
            hospital_id=str(provider.hospital_id),
            access_expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        )
        seed_request(redis, request)
        with pytest.raises(HTTPException) as expired:
            await claim_approved_access("request-1", Response(), provider, MagicMock())
        assert expired.value.status_code == 403
