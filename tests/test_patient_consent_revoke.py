from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.v2.consent_routes import revoke_patient_approved_access
from app.core.consent_gate import validate_consent_for_patient
from app.models.provider import AffiliationType
from app.models.provider_context import (
    AffiliationContext,
    HospitalContext,
    ProviderContext,
    ProviderIdentityContext,
)
from app.security.audit_context import AuditContext, AuditDomain
from app.services.approved_access_capability import (
    CAPABILITY_PREFIX,
    CLAIM_PREFIX,
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


def _provider(provider_id: str, hospital_id: str) -> ProviderContext:
    return ProviderContext(
        provider=ProviderIdentityContext(
            provider_id=uuid.UUID(provider_id),
            display_name="Provider",
            contact_email="provider@example.test",
        ),
        hospital=HospitalContext(
            hospital_id=uuid.UUID(hospital_id),
            facility_code="TEST",
            display_name="Test Hospital",
        ),
        affiliation=AffiliationContext(
            affiliation_id=uuid.uuid4(),
            affiliation_type=AffiliationType.PERMANENT,
            roles=["clinician"],
            is_primary=True,
        ),
    )


def _database(grant):
    result = MagicMock()
    result.scalars.return_value.all.return_value = [grant]
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_patient_revoke_invalidates_capability_and_next_validation_is_forbidden():
    request_id = "request-1"
    patient_id = "patient-1"
    provider_id = "00000000-0000-0000-0000-000000000001"
    hospital_id = "00000000-0000-0000-0000-000000000002"
    request_data = {
        "request_id": request_id,
        "provider_id": provider_id,
        "hospital_id": hospital_id,
        "patient_id": patient_id,
        "purpose": "treatment",
        "scope": "clinical",
        "status": "approved",
        "access_expires_at": (
            datetime.now(timezone.utc) + timedelta(minutes=10)
        ).isoformat(),
    }
    redis = MemoryRedis()
    redis.data[f"consent_request:{request_id}"] = json.dumps(request_data)
    grant = SimpleNamespace(
        patient_id=patient_id,
        clinician_id=provider_id,
        hospital_id=hospital_id,
        revoked_at=None,
        revoked_reason=None,
    )
    db = _database(grant)
    audit = AsyncMock()
    audit_context = AuditContext.for_tenant(
        tenant_id=patient_id,
        domain=AuditDomain.CONSENT,
    )

    with patch(
        "app.services.approved_access_capability.get_async_redis_client",
        return_value=redis,
    ):
        token, _capability = await issue_from_approved_request(
            request_data=request_data
        )
        digest = token_hash(token)
        assert f"{CAPABILITY_PREFIX}{digest}" in redis.data
        assert f"{CLAIM_PREFIX}{request_id}" in redis.data

        with (
            patch(
                "app.api.v2.consent_routes.get_redis_client",
                return_value=redis,
            ),
            patch(
                "app.api.v2.consent_routes.append_audit_log_or_503",
                audit,
            ),
            patch(
                "app.api.v2.consent_routes.current_audit_context",
                return_value=audit_context,
            ),
        ):
            response = await revoke_patient_approved_access(
                request_id,
                patient_id,
                db,
            )

        assert response.request_id == request_id
        assert response.status == "revoked"
        assert response.revoked_at
        assert f"{CAPABILITY_PREFIX}{digest}" not in redis.data
        assert f"{CLAIM_PREFIX}{request_id}" not in redis.data
        assert json.loads(redis.data[f"consent_request:{request_id}"])["status"] == (
            "revoked"
        )
        assert grant.revoked_at is not None
        assert grant.revoked_reason == "patient_revoked"
        db.commit.assert_awaited_once()
        assert audit.await_args.kwargs["event_type"] == "PATIENT_CONSENT_REVOKED"
        assert audit.await_args.kwargs["target_id"] == request_id

        assert (
            await validate(
                token=token,
                patient_id=patient_id,
                provider_id=provider_id,
                hospital_id=hospital_id,
                requested_category="clinical_summary",
            )
            is None
        )

        with (
            patch(
                "app.core.consent_gate.validate_consent_capability",
                AsyncMock(return_value=None),
            ),
            patch(
                "app.core.consent_gate.append_audit_log_or_503",
                AsyncMock(),
            ),
            patch(
                "app.core.consent_gate.current_audit_context",
                return_value=audit_context,
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                await validate_consent_for_patient(
                    patient_id,
                    "clinical_summary",
                    _provider(provider_id, hospital_id),
                    token,
                )
        assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_patient_revoke_is_idempotent():
    request_id = "request-2"
    patient_id = "patient-2"
    revoked_at = datetime.now(timezone.utc)
    request_data = {
        "request_id": request_id,
        "provider_id": "provider-2",
        "hospital_id": "hospital-2",
        "patient_id": patient_id,
        "status": "revoked",
        "revoked_at": revoked_at.isoformat(),
    }
    redis = MemoryRedis()
    redis.data[f"consent_request:{request_id}"] = json.dumps(request_data)
    grant = SimpleNamespace(
        patient_id=patient_id,
        clinician_id="provider-2",
        hospital_id="hospital-2",
        revoked_at=revoked_at,
        revoked_reason="patient_revoked",
    )
    db = _database(grant)
    audit = AsyncMock()

    with (
        patch(
            "app.services.approved_access_capability.get_async_redis_client",
            return_value=redis,
        ),
        patch(
            "app.api.v2.consent_routes.get_redis_client",
            return_value=redis,
        ),
        patch(
            "app.api.v2.consent_routes.append_audit_log_or_503",
            audit,
        ),
        patch(
            "app.api.v2.consent_routes.current_audit_context",
            return_value=AuditContext.for_tenant(
                tenant_id=patient_id,
                domain=AuditDomain.CONSENT,
            ),
        ),
    ):
        first = await revoke_patient_approved_access(request_id, patient_id, db)
        second = await revoke_patient_approved_access(request_id, patient_id, db)

    assert first.revoked_at == revoked_at.isoformat()
    assert second.revoked_at == first.revoked_at
    assert grant.revoked_at == revoked_at
    assert audit.await_count == 2
    assert {call.kwargs["idempotency_key"] for call in audit.await_args_list} == {
        f"patient-consent-revoked:{request_id}"
    }


@pytest.mark.asyncio
async def test_patient_cannot_revoke_another_patients_request():
    request_id = "request-3"
    owner_patient_id = "patient-owner"
    redis = MemoryRedis()
    redis.data[f"consent_request:{request_id}"] = json.dumps(
        {
            "request_id": request_id,
            "provider_id": "provider-3",
            "hospital_id": "hospital-3",
            "patient_id": owner_patient_id,
            "status": "approved",
        }
    )
    grant = SimpleNamespace(
        patient_id=owner_patient_id,
        clinician_id="provider-3",
        hospital_id="hospital-3",
        revoked_at=None,
        revoked_reason=None,
    )
    db = _database(grant)

    with (
        patch(
            "app.api.v2.consent_routes.get_redis_client",
            return_value=redis,
        ),
        patch(
            "app.api.v2.consent_routes.invalidate_request",
            AsyncMock(),
        ) as invalidate,
    ):
        with pytest.raises(HTTPException) as exc:
            await revoke_patient_approved_access(
                request_id,
                "patient-attacker",
                db,
            )

    assert exc.value.status_code == 403
    invalidate.assert_not_awaited()
    db.commit.assert_not_awaited()
    assert grant.revoked_at is None
