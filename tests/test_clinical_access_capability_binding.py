from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.security.clinical_access_policy import (
    CLINICAL_ACCESS_POLICY_VERSION,
    ClinicalAccessOperation,
)
from app.security.document_processing_policy import (
    DOCUMENT_PROCESSING_GRANT_TYPE,
    DocumentProcessingOperation,
)
from app.services.approved_access_capability import (
    CAPABILITY_PREFIX,
    CLINICAL_ACCESS_SESSION_GRANT_TYPE,
    issue_from_approved_request,
    token_hash,
    validate,
    validate_document_processing_access,
)


class MemoryRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str):
        return self.data.get(key)

    async def set(self, key: str, value: str, **_kwargs):
        self.data[key] = value
        return True

    async def delete(self, key: str):
        self.data.pop(key, None)
        return 1

    async def eval(
        self,
        _script: str,
        _numkeys: int,
        claim_key: str,
        capability_key: str,
        payload: str,
        digest: str,
        _ttl: int,
    ):
        if claim_key in self.data:
            return 0
        self.data[capability_key] = payload
        self.data[claim_key] = digest
        return 1


def _approved_v3_request(**overrides) -> dict:
    request = {
        "protocol_version": "nexa-consent-v3",
        "request_id": "request-v3-1",
        "provider_id": "provider-1",
        "hospital_id": "hospital-1",
        "patient_id": "patient-1",
        "purpose": "routine_checkup",
        "scope": "clinical",
        "status": "approved",
        "access_expires_at": (
            datetime.now(timezone.utc) + timedelta(minutes=10)
        ).isoformat(),
    }
    request.update(overrides)
    return request


def _seed_request(redis: MemoryRedis, request: dict) -> None:
    redis.data[f"consent_request:{request['request_id']}"] = json.dumps(request)


@pytest.mark.asyncio
async def test_v3_routine_claim_is_bound_to_exact_provider_session() -> None:
    redis = MemoryRedis()
    request = _approved_v3_request()
    _seed_request(redis, request)

    with patch(
        "app.services.approved_access_capability.get_async_redis_client",
        return_value=redis,
    ):
        token, capability = await issue_from_approved_request(
            request_data=request,
            provider_session_binding="provider-binding-a",
        )

        assert capability.grant_type == CLINICAL_ACCESS_SESSION_GRANT_TYPE
        assert capability.clinical_session_id
        assert capability.clinical_access_policy_version == CLINICAL_ACCESS_POLICY_VERSION
        assert capability.allowed_operations == (
            ClinicalAccessOperation.READ_CLINICAL_HISTORY.value,
        )

        stored = json.loads(redis.data[f"{CAPABILITY_PREFIX}{token_hash(token)}"])
        assert stored["clinical_session_id"] == capability.clinical_session_id
        assert stored["provider_session_binding_hash"] != "provider-binding-a"
        assert "provider-binding-a" not in json.dumps(stored)
        assert stored["allowed_operations"] == [
            ClinicalAccessOperation.READ_CLINICAL_HISTORY.value
        ]

        assert (
            await validate(
                token=token,
                patient_id="patient-1",
                provider_id="provider-1",
                hospital_id="hospital-1",
                requested_category="clinical_summary",
                provider_session_binding="provider-binding-a",
            )
            is not None
        )
        assert (
            await validate(
                token=token,
                patient_id="patient-1",
                provider_id="provider-1",
                hospital_id="hospital-1",
                requested_category="clinical_summary",
                provider_session_binding="provider-binding-b",
            )
            is None
        )
        assert (
            await validate(
                token=token,
                patient_id="patient-1",
                provider_id="provider-1",
                hospital_id="hospital-1",
                requested_category="clinical_summary",
                provider_session_binding=None,
            )
            is None
        )


@pytest.mark.asyncio
async def test_v3_session_operation_widening_in_redis_fails_closed() -> None:
    redis = MemoryRedis()
    request = _approved_v3_request()
    _seed_request(redis, request)

    with patch(
        "app.services.approved_access_capability.get_async_redis_client",
        return_value=redis,
    ):
        token, _ = await issue_from_approved_request(
            request_data=request,
            provider_session_binding="provider-binding-a",
        )
        capability_key = f"{CAPABILITY_PREFIX}{token_hash(token)}"
        stored = json.loads(redis.data[capability_key])
        stored["allowed_operations"].append(
            ClinicalAccessOperation.WRITE_PRESCRIPTION.value
        )
        redis.data[capability_key] = json.dumps(stored)

        assert (
            await validate(
                token=token,
                patient_id="patient-1",
                provider_id="provider-1",
                hospital_id="hospital-1",
                requested_category="clinical_summary",
                provider_session_binding="provider-binding-a",
            )
            is None
        )


@pytest.mark.asyncio
async def test_v3_session_never_authorizes_unmapped_routine_category() -> None:
    redis = MemoryRedis()
    request = _approved_v3_request(scope="full")
    _seed_request(redis, request)

    with patch(
        "app.services.approved_access_capability.get_async_redis_client",
        return_value=redis,
    ):
        token, _ = await issue_from_approved_request(
            request_data=request,
            provider_session_binding="provider-binding-a",
        )

        assert (
            await validate(
                token=token,
                patient_id="patient-1",
                provider_id="provider-1",
                hospital_id="hospital-1",
                requested_category="policy_update",
                provider_session_binding="provider-binding-a",
            )
            is None
        )


@pytest.mark.asyncio
async def test_document_processing_remains_a_distinct_grant_type() -> None:
    redis = MemoryRedis()
    request = _approved_v3_request(
        request_id="request-doc-1",
        purpose="document_processing",
        scope="documents",
    )
    _seed_request(redis, request)

    with patch(
        "app.services.approved_access_capability.get_async_redis_client",
        return_value=redis,
    ):
        token, capability = await issue_from_approved_request(request_data=request)

        assert capability.grant_type == DOCUMENT_PROCESSING_GRANT_TYPE
        assert capability.clinical_session_id is None
        assert capability.provider_session_binding_hash is None
        assert (
            await validate_document_processing_access(
                token=token,
                patient_id="patient-1",
                provider_id="provider-1",
                hospital_id="hospital-1",
                required_operation=DocumentProcessingOperation.UPLOAD_DOCUMENT,
                expected_request_id="request-doc-1",
            )
            is not None
        )
        assert (
            await validate(
                token=token,
                patient_id="patient-1",
                provider_id="provider-1",
                hospital_id="hospital-1",
                requested_category="clinical_summary",
                provider_session_binding="provider-binding-a",
            )
            is None
        )
