from __future__ import annotations

import inspect
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core import clinical_session_gate as gate
from app.security.clinical_access_policy import (
    CLINICAL_ACCESS_POLICY_VERSION,
    ClinicalAccessOperation,
)
from app.services.signed_treatment_session_v1 import (
    SIGNED_TREATMENT_SESSION_V1_PROTOCOL_VERSION,
    normalize_treatment_operations,
)
from app.services.treatment_session_v1_mint import (
    TREATMENT_CAPABILITY_PREFIX,
    TREATMENT_SESSION_V1_GRANT_TYPE,
    TREATMENT_SESSION_V1_SCOPE,
    provider_session_binding_hash,
    treatment_token_hash,
)


class _Result:
    def __init__(self, row):
        self.row = row

    def scalar_one_or_none(self):
        return self.row


class _DB:
    def __init__(self, *rows):
        self.rows = list(rows)
        self.flushed = False

    async def execute(self, _statement):
        if not self.rows:
            raise AssertionError("unexpected database query")
        return _Result(self.rows.pop(0))

    async def flush(self):
        self.flushed = True


class _Redis:
    def __init__(self, values: dict[str, str] | None = None, *, fail: bool = False):
        self.values = values or {}
        self.fail = fail

    async def get(self, key: str):
        if self.fail:
            raise RuntimeError("redis unavailable")
        return self.values.get(key)


def _fixture(*, operations: list[str] | None = None):
    now = datetime.now(timezone.utc)
    issued_at = now - timedelta(seconds=5)
    expires_at = now + timedelta(minutes=10)
    token = "opaque-treatment-token"
    token_digest = treatment_token_hash(token)
    session_id = uuid.uuid4()
    request_id = uuid.uuid4()
    patient_id = uuid.uuid4()
    provider_id = uuid.uuid4()
    hospital_id = uuid.uuid4()
    binding = "provider-session-a"
    binding_hash = provider_session_binding_hash(binding)
    operation_values = list(
        normalize_treatment_operations(
            operations
            or [
                ClinicalAccessOperation.CREATE_ENCOUNTER.value,
                ClinicalAccessOperation.READ_CLINICAL_HISTORY.value,
                ClinicalAccessOperation.WRITE_PRESCRIPTION.value,
            ]
        )
    )

    payload = {
        "protocol_version": SIGNED_TREATMENT_SESSION_V1_PROTOCOL_VERSION,
        "grant_type": TREATMENT_SESSION_V1_GRANT_TYPE,
        "session_id": str(session_id),
        "request_id": str(request_id),
        "patient_id": str(patient_id),
        "provider_id": str(provider_id),
        "hospital_id": str(hospital_id),
        "purpose": "treatment",
        "scope": TREATMENT_SESSION_V1_SCOPE,
        "allowed_operations": operation_values,
        "provider_session_binding_hash": binding_hash,
        "clinical_access_policy_version": CLINICAL_ACCESS_POLICY_VERSION,
        "issued_at": issued_at.isoformat(),
        "expires_at": expires_at.isoformat(),
    }
    redis = _Redis(
        {f"{TREATMENT_CAPABILITY_PREFIX}{token_digest}": json.dumps(payload)}
    )
    session = SimpleNamespace(
        session_id=session_id,
        patient_id=patient_id,
        provider_id=provider_id,
        hospital_id=hospital_id,
        consent_request_id=str(request_id),
        token_hash=token_digest,
        purpose="treatment",
        scope=TREATMENT_SESSION_V1_SCOPE,
        allowed_operations=operation_values.copy(),
        provider_session_binding_hash=binding_hash,
        policy_version=CLINICAL_ACCESS_POLICY_VERSION,
        issued_at=issued_at,
        expires_at=expires_at,
        status="ACTIVE",
        encounter_id=None,
        revoked_at=None,
        revocation_reason=None,
    )
    grant = SimpleNamespace(
        token_hash=token_digest,
        patient_id=str(patient_id),
        clinician_id=str(provider_id),
        hospital_id=hospital_id,
        purpose="treatment",
        scope=[TREATMENT_SESSION_V1_SCOPE],
        is_break_glass=False,
        issued_at=now,
        expires_at=expires_at,
        revoked_at=None,
        assurance_level="signed_device_treatment_v1",
        request_id=str(request_id),
    )
    provider = SimpleNamespace(
        actor_uid=str(provider_id),
        hospital_id=hospital_id,
        session_binding=binding,
    )
    return SimpleNamespace(
        now=now,
        token=token,
        token_digest=token_digest,
        redis=redis,
        payload=payload,
        session=session,
        grant=grant,
        provider=provider,
    )


@pytest.mark.asyncio
async def test_gate_requires_exact_live_and_durable_authority(monkeypatch):
    data = _fixture()
    monkeypatch.setattr(gate, "get_async_redis_client", lambda: data.redis)

    authority = await gate.validate_treatment_session_v1(
        db=_DB(data.session, data.grant),
        token=data.token,
        provider=data.provider,
        required_operation=ClinicalAccessOperation.CREATE_ENCOUNTER,
        now=data.now,
    )

    assert authority.session_id == data.session.session_id
    assert authority.patient_id == data.session.patient_id
    assert authority.provider_id == data.session.provider_id
    assert authority.hospital_id == data.session.hospital_id
    assert authority.required_operation is ClinicalAccessOperation.CREATE_ENCOUNTER
    assert authority.token_hash == data.token_digest
    assert authority.encounter_id is None
    assert data.token not in repr(authority)


@pytest.mark.asyncio
async def test_gate_rejects_operation_not_patient_signed(monkeypatch):
    data = _fixture(
        operations=[ClinicalAccessOperation.READ_CLINICAL_HISTORY.value]
    )
    monkeypatch.setattr(gate, "get_async_redis_client", lambda: data.redis)

    with pytest.raises(gate.TreatmentSessionV1GateDenied) as caught:
        await gate.validate_treatment_session_v1(
            db=_DB(data.session, data.grant),
            token=data.token,
            provider=data.provider,
            required_operation=ClinicalAccessOperation.WRITE_PRESCRIPTION,
            now=data.now,
        )

    assert caught.value.code == "TREATMENT_OPERATION_NOT_AUTHORIZED"


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["provider", "hospital", "session"])
async def test_gate_rejects_current_provider_context_mismatch(monkeypatch, mutation):
    data = _fixture()
    monkeypatch.setattr(gate, "get_async_redis_client", lambda: data.redis)
    provider = SimpleNamespace(
        actor_uid=data.provider.actor_uid,
        hospital_id=data.provider.hospital_id,
        session_binding=data.provider.session_binding,
    )
    if mutation == "provider":
        provider.actor_uid = str(uuid.uuid4())
    elif mutation == "hospital":
        provider.hospital_id = uuid.uuid4()
    else:
        provider.session_binding = "provider-session-b"

    with pytest.raises(gate.TreatmentSessionV1GateDenied):
        await gate.validate_treatment_session_v1(
            db=_DB(data.session, data.grant),
            token=data.token,
            provider=provider,
            required_operation=ClinicalAccessOperation.CREATE_ENCOUNTER,
            now=data.now,
        )


@pytest.mark.asyncio
async def test_gate_rejects_redis_to_durable_operation_disagreement(monkeypatch):
    data = _fixture()
    data.session.allowed_operations = [
        ClinicalAccessOperation.READ_CLINICAL_HISTORY.value
    ]
    monkeypatch.setattr(gate, "get_async_redis_client", lambda: data.redis)

    with pytest.raises(gate.TreatmentSessionV1GateDenied):
        await gate.validate_treatment_session_v1(
            db=_DB(data.session, data.grant),
            token=data.token,
            provider=data.provider,
            required_operation=ClinicalAccessOperation.CREATE_ENCOUNTER,
            now=data.now,
        )


@pytest.mark.asyncio
async def test_gate_rejects_revoked_durable_grant(monkeypatch):
    data = _fixture()
    data.grant.revoked_at = data.now
    monkeypatch.setattr(gate, "get_async_redis_client", lambda: data.redis)

    with pytest.raises(gate.TreatmentSessionV1GateDenied):
        await gate.validate_treatment_session_v1(
            db=_DB(data.session, data.grant),
            token=data.token,
            provider=data.provider,
            required_operation=ClinicalAccessOperation.CREATE_ENCOUNTER,
            now=data.now,
        )


@pytest.mark.asyncio
async def test_gate_rejects_encounter_binding_injected_into_redis(monkeypatch):
    data = _fixture()
    payload = dict(data.payload)
    payload["encounter_id"] = str(uuid.uuid4())
    data.redis.values[f"{TREATMENT_CAPABILITY_PREFIX}{data.token_digest}"] = json.dumps(
        payload
    )
    monkeypatch.setattr(gate, "get_async_redis_client", lambda: data.redis)

    with pytest.raises(gate.TreatmentSessionV1GateDenied):
        await gate.validate_treatment_session_v1(
            db=_DB(data.session, data.grant),
            token=data.token,
            provider=data.provider,
            required_operation=ClinicalAccessOperation.CREATE_ENCOUNTER,
            now=data.now,
        )


@pytest.mark.asyncio
async def test_gate_fails_closed_when_redis_is_unavailable(monkeypatch):
    data = _fixture()
    monkeypatch.setattr(
        gate,
        "get_async_redis_client",
        lambda: _Redis(fail=True),
    )

    with pytest.raises(gate.TreatmentSessionV1GateUnavailable) as caught:
        await gate.validate_treatment_session_v1(
            db=_DB(data.session, data.grant),
            token=data.token,
            provider=data.provider,
            required_operation=ClinicalAccessOperation.CREATE_ENCOUNTER,
            now=data.now,
        )

    assert caught.value.code == "TREATMENT_SESSION_SECURITY_STORE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_gate_loads_existing_encounter_binding_only_from_postgres(monkeypatch):
    data = _fixture()
    encounter_id = uuid.uuid4()
    data.session.encounter_id = str(encounter_id)
    monkeypatch.setattr(gate, "get_async_redis_client", lambda: data.redis)

    authority = await gate.validate_treatment_session_v1(
        db=_DB(data.session, data.grant),
        token=data.token,
        provider=data.provider,
        required_operation=ClinicalAccessOperation.READ_CLINICAL_HISTORY,
        now=data.now,
    )

    assert authority.encounter_id == encounter_id


@pytest.mark.asyncio
async def test_encounter_binding_is_server_generated_and_idempotent(monkeypatch):
    data = _fixture()
    monkeypatch.setattr(gate, "get_async_redis_client", lambda: data.redis)
    authority = await gate.validate_treatment_session_v1(
        db=_DB(data.session, data.grant),
        token=data.token,
        provider=data.provider,
        required_operation=ClinicalAccessOperation.CREATE_ENCOUNTER,
        now=data.now,
    )

    first_db = _DB(data.session)
    first = await gate.stage_server_encounter_binding(
        db=first_db,
        authority=authority,
    )
    assert first_db.flushed
    assert data.session.encounter_id == str(first)

    second = await gate.stage_server_encounter_binding(
        db=_DB(data.session),
        authority=authority,
    )
    assert second == first


@pytest.mark.asyncio
async def test_encounter_binding_requires_create_encounter_operation(monkeypatch):
    data = _fixture()
    monkeypatch.setattr(gate, "get_async_redis_client", lambda: data.redis)
    authority = await gate.validate_treatment_session_v1(
        db=_DB(data.session, data.grant),
        token=data.token,
        provider=data.provider,
        required_operation=ClinicalAccessOperation.READ_CLINICAL_HISTORY,
        now=data.now,
    )

    with pytest.raises(gate.TreatmentSessionV1GateDenied) as caught:
        await gate.stage_server_encounter_binding(
            db=_DB(data.session),
            authority=authority,
        )
    assert caught.value.code == "TREATMENT_ENCOUNTER_OPERATION_REQUIRED"


def test_encounter_binding_api_accepts_no_client_selected_encounter_id():
    parameters = inspect.signature(gate.stage_server_encounter_binding).parameters
    assert set(parameters) == {"db", "authority"}


def test_gate_uses_dedicated_header_and_is_not_wired_to_write_routes_yet():
    dependency = gate.require_clinical_session(
        ClinicalAccessOperation.WRITE_PRESCRIPTION
    )
    header_default = inspect.signature(dependency).parameters[
        "x_treatment_token"
    ].default
    assert header_default.alias == "X-Treatment-Token"

    route_source = Path("app/api/v2/patient_record_routes.py").read_text(
        encoding="utf-8"
    )
    assert "require_clinical_session" not in route_source
    assert "X-Treatment-Token" not in route_source


def test_gate_factory_rejects_untyped_operation():
    with pytest.raises(TypeError):
        gate.require_clinical_session("WRITE_PRESCRIPTION")  # type: ignore[arg-type]
