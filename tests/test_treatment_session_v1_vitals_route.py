from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request, Response

from app.api.v2 import treatment_session_v1_vitals_routes as routes
from app.core import clinical_session_gate as gate
from app.core import dependencies as clinical_dependencies
from app.core.clinical_session_gate import TreatmentSessionV1Authority
from app.security.audit_context import AuditDomain
from app.security.clinical_access_policy import (
    CLINICAL_ACCESS_POLICY_VERSION,
    ClinicalAccessOperation,
)
from app.security.provider_capabilities import ClinicalCapability
from app.services.treatment_vitals import (
    TreatmentVitalIdempotencyConflict,
    TreatmentVitalType,
    TreatmentVitalUnavailable,
    TreatmentVitalValidationError,
    TreatmentVitalWriteResult,
)


class _DB:
    def __init__(
        self,
        *,
        fail_commit: bool = False,
        events: list[str] | None = None,
    ):
        self.fail_commit = fail_commit
        self.events = events if events is not None else []
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.events.append("commit")
        self.commits += 1
        if self.fail_commit:
            raise RuntimeError("commit unavailable")

    async def rollback(self) -> None:
        self.events.append("rollback")
        self.rollbacks += 1


def _authority(
    *,
    binding: str = "provider-session-a",
    operation: ClinicalAccessOperation = ClinicalAccessOperation.WRITE_VITALS,
) -> TreatmentSessionV1Authority:
    now = datetime.now(timezone.utc)
    return TreatmentSessionV1Authority(
        session_id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        patient_id=uuid.uuid4(),
        provider_id=uuid.uuid4(),
        hospital_id=uuid.uuid4(),
        purpose="treatment",
        allowed_operations=(ClinicalAccessOperation.WRITE_VITALS.value,),
        required_operation=operation,
        provider_session_binding_hash=hashlib.sha256(
            binding.encode("utf-8")
        ).hexdigest(),
        policy_version=CLINICAL_ACCESS_POLICY_VERSION,
        token_hash="b" * 64,
        issued_at=now - timedelta(seconds=5),
        expires_at=now + timedelta(minutes=10),
        encounter_id=uuid.uuid4(),
    )


def _provider(
    authority: TreatmentSessionV1Authority,
    *,
    binding: str = "provider-session-a",
):
    return SimpleNamespace(
        actor_uid=str(authority.provider_id),
        hospital_id=authority.hospital_id,
        session_binding=binding,
    )


def _request(payload: object) -> Request:
    body = json.dumps(payload).encode("utf-8")
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.request", "body": b"", "more_body": False}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/v2/treatment-session/v1/vitals",
            "raw_path": b"/api/v2/treatment-session/v1/vitals",
            "query_string": b"",
            "headers": [(b"authorization", b"Bearer provider-session-a")],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        },
        receive,
    )


def _heart_rate_payload(**extra):
    payload = {
        "kind": "heart_rate",
        "beats_per_minute": 72,
        "recorded_at": "2026-09-18T12:30:00+05:30",
    }
    payload.update(extra)
    return payload


def _result(
    authority: TreatmentSessionV1Authority,
    *,
    replay: bool = False,
) -> TreatmentVitalWriteResult:
    return TreatmentVitalWriteResult(
        record_id=uuid.uuid4(),
        encounter_id=authority.encounter_id,
        vital_type=TreatmentVitalType.HEART_RATE,
        recorded_at=datetime(2026, 9, 18, 7, 0, tzinfo=timezone.utc),
        idempotent_replay=replay,
    )


def test_isolated_router_exposes_exactly_one_bounded_write_surface():
    paths = {
        (tuple(sorted(route.methods or ())), route.path)
        for route in routes.router.routes
    }
    assert paths == {
        (("POST",), "/api/v2/treatment-session/v1/vitals"),
    }


def test_isolated_router_is_not_mounted_in_app_before_task1_merge():
    from app.main import app

    assert "/api/v2/treatment-session/v1/vitals" not in {
        route.path for route in app.routes
    }


@pytest.mark.parametrize(
    ("payload", "expected_type", "expected_value", "expected_unit"),
    [
        (
            {
                "kind": "blood_pressure",
                "systolic_bp": 120,
                "diastolic_bp": 80,
                "recorded_at": "2026-09-18T12:30:00+05:30",
            },
            TreatmentVitalType.BLOOD_PRESSURE,
            "120/80",
            "mmHg",
        ),
        (
            _heart_rate_payload(),
            TreatmentVitalType.HEART_RATE,
            "72",
            "bpm",
        ),
        (
            {
                "kind": "temperature",
                "celsius": 36.5,
                "recorded_at": "2026-09-18T12:30:00+05:30",
            },
            TreatmentVitalType.TEMPERATURE,
            "36.5",
            "C",
        ),
        (
            {
                "kind": "spo2",
                "percentage": 98,
                "recorded_at": "2026-09-18T12:30:00+05:30",
            },
            TreatmentVitalType.SPO2,
            "98",
            "%",
        ),
    ],
)
def test_discriminated_request_contract_builds_server_owned_observation(
    payload,
    expected_type,
    expected_value,
    expected_unit,
):
    parsed = routes._parse_request(json.dumps(payload).encode("utf-8"))
    observation = routes._observation_from_request(parsed)

    assert observation.vital_type is expected_type
    assert observation.value == expected_value
    assert observation.unit == expected_unit
    assert observation.recorded_at == datetime(
        2026, 9, 18, 7, 0, tzinfo=timezone.utc
    )


@pytest.mark.parametrize(
    "field",
    [
        "patient_id",
        "provider_id",
        "hospital_id",
        "encounter_id",
        "clinical_session_id",
        "operation",
        "source",
        "confidence",
        "risk_level",
    ],
)
def test_caller_authority_and_provenance_injection_is_rejected(field):
    payload = _heart_rate_payload(**{field: "caller-controlled"})

    with pytest.raises(TreatmentVitalValidationError) as caught:
        routes._parse_request(json.dumps(payload).encode("utf-8"))

    assert caught.value.code == "TREATMENT_VITAL_REQUEST_INVALID"


def test_naive_recorded_at_is_rejected():
    payload = _heart_rate_payload(recorded_at="2026-09-18T12:30:00")
    parsed = routes._parse_request(json.dumps(payload).encode("utf-8"))

    with pytest.raises(TreatmentVitalValidationError) as caught:
        routes._observation_from_request(parsed)

    assert caught.value.code == "TREATMENT_VITAL_RECORDED_AT_INVALID"


@pytest.mark.asyncio
async def test_entry_provider_dependency_uses_current_clinical_trust(monkeypatch):
    authority = _authority()
    provider = _provider(authority)
    db = _DB()
    observed = {}

    async def deny_current_trust(*, request, provider, db, capability):
        observed["capability"] = capability
        raise HTTPException(
            status_code=403,
            detail={"error_code": "CLINICAL_ELIGIBILITY_DENIED"},
        )

    monkeypatch.setattr(
        clinical_dependencies,
        "enforce_current_clinical_capability",
        deny_current_trust,
    )

    with pytest.raises(HTTPException) as caught:
        await routes._ENTRY_PROVIDER_DEPENDENCY(
            request=_request(_heart_rate_payload()),
            provider=provider,
            db=db,
        )

    assert caught.value.status_code == 403
    assert caught.value.detail == {
        "error_code": "CLINICAL_ELIGIBILITY_DENIED"
    }
    assert observed["capability"] is ClinicalCapability.RECORD_READ


@pytest.mark.asyncio
async def test_missing_treatment_token_maps_to_stable_denial(monkeypatch):
    authority = _authority()
    provider = _provider(authority)

    async def deny_missing_token(*, db, token, provider, required_operation):
        assert token is None
        assert required_operation is ClinicalAccessOperation.WRITE_VITALS
        raise gate.TreatmentSessionV1GateDenied("TREATMENT_SESSION_REQUIRED")

    monkeypatch.setattr(
        gate,
        "validate_treatment_session_v1",
        deny_missing_token,
    )

    with pytest.raises(HTTPException) as caught:
        await routes._WRITE_VITALS_AUTHORITY_DEPENDENCY(
            x_treatment_token=None,
            provider=provider,
            db=_DB(),
        )

    assert caught.value.status_code == 403
    assert caught.value.detail == {
        "error_code": "TREATMENT_SESSION_REQUIRED"
    }


@pytest.mark.asyncio
async def test_wrong_operation_maps_to_stable_denial(monkeypatch):
    authority = _authority()
    provider = _provider(authority)

    async def deny_wrong_operation(*, db, token, provider, required_operation):
        assert token == "wrong-operation-token"
        assert required_operation is ClinicalAccessOperation.WRITE_VITALS
        raise gate.TreatmentSessionV1GateDenied(
            "TREATMENT_OPERATION_NOT_AUTHORIZED"
        )

    monkeypatch.setattr(
        gate,
        "validate_treatment_session_v1",
        deny_wrong_operation,
    )

    with pytest.raises(HTTPException) as caught:
        await routes._WRITE_VITALS_AUTHORITY_DEPENDENCY(
            x_treatment_token="wrong-operation-token",
            provider=provider,
            db=_DB(),
        )

    assert caught.value.status_code == 403
    assert caught.value.detail == {
        "error_code": "TREATMENT_OPERATION_NOT_AUTHORIZED"
    }


@pytest.mark.asyncio
async def test_valid_write_stages_then_rechecks_trust_then_commits(monkeypatch):
    events = []
    db = _DB(events=events)
    authority = _authority()
    provider = _provider(authority)
    expected = _result(authority)
    captured = {}

    async def stage(**kwargs):
        events.append("stage")
        captured.update(kwargs)
        return expected

    async def final_trust(**kwargs):
        events.append("final_trust")
        assert kwargs["capability"] is ClinicalCapability.RECORD_READ
        return provider

    monkeypatch.setattr(routes, "stage_treatment_vital_write", stage)
    monkeypatch.setattr(
        routes,
        "enforce_current_clinical_capability",
        final_trust,
    )

    response = Response()
    result = await routes.write_treatment_vital(
        request=_request(_heart_rate_payload()),
        response=response,
        idempotency_key="vitals-route-0001",
        provider=provider,
        authority=authority,
        db=db,
    )

    assert events == ["stage", "final_trust", "commit"]
    assert db.commits == 1
    assert db.rollbacks == 0
    assert response.headers["cache-control"] == "no-store"
    assert result.status == "committed"
    assert result.record_id == str(expected.record_id)
    assert result.encounter_id == str(authority.encounter_id)
    assert result.idempotent_replay is False
    assert captured["authority"] is authority
    assert captured["idempotency_key"] == "vitals-route-0001"
    assert captured["audit_context"].hospital_id == str(authority.hospital_id)
    assert captured["audit_context"].domain is AuditDomain.PATIENT_RECORD
    assert captured["observation"].value == "72"


@pytest.mark.asyncio
async def test_idempotent_replay_keeps_http_200_logical_result(monkeypatch):
    db = _DB()
    authority = _authority()
    provider = _provider(authority)
    expected = _result(authority, replay=True)

    async def stage(**_kwargs):
        return expected

    async def final_trust(**_kwargs):
        return provider

    monkeypatch.setattr(routes, "stage_treatment_vital_write", stage)
    monkeypatch.setattr(
        routes,
        "enforce_current_clinical_capability",
        final_trust,
    )

    result = await routes.write_treatment_vital(
        request=_request(_heart_rate_payload()),
        response=Response(),
        idempotency_key="vitals-route-0002",
        provider=provider,
        authority=authority,
        db=db,
    )

    route = routes.router.routes[0]
    assert route.status_code == 200
    assert result.record_id == str(expected.record_id)
    assert result.idempotent_replay is True
    assert db.commits == 1


@pytest.mark.asyncio
async def test_invalid_idempotency_key_is_value_free_and_rolls_back(monkeypatch):
    db = _DB()
    authority = _authority()
    provider = _provider(authority)

    async def should_not_stage(**_kwargs):
        raise AssertionError("staging must not run")

    monkeypatch.setattr(
        routes,
        "stage_treatment_vital_write",
        should_not_stage,
    )

    with pytest.raises(HTTPException) as caught:
        await routes.write_treatment_vital(
            request=_request(_heart_rate_payload()),
            response=Response(),
            idempotency_key=None,
            provider=provider,
            authority=authority,
            db=db,
        )

    assert caught.value.status_code == 422
    assert caught.value.detail == {
        "error_code": "TREATMENT_VITAL_IDEMPOTENCY_KEY_INVALID"
    }
    assert db.commits == 0
    assert db.rollbacks == 1


@pytest.mark.asyncio
async def test_semantic_idempotency_conflict_rolls_back(monkeypatch):
    db = _DB()
    authority = _authority()
    provider = _provider(authority)

    async def conflict(**_kwargs):
        raise TreatmentVitalIdempotencyConflict(
            "TREATMENT_VITAL_IDEMPOTENCY_KEY_REUSED"
        )

    monkeypatch.setattr(routes, "stage_treatment_vital_write", conflict)

    with pytest.raises(HTTPException) as caught:
        await routes.write_treatment_vital(
            request=_request(_heart_rate_payload()),
            response=Response(),
            idempotency_key="vitals-route-0003",
            provider=provider,
            authority=authority,
            db=db,
        )

    assert caught.value.status_code == 409
    assert caught.value.detail == {
        "error_code": "TREATMENT_VITAL_IDEMPOTENCY_KEY_REUSED"
    }
    assert db.commits == 0
    assert db.rollbacks == 1


@pytest.mark.asyncio
async def test_staging_failure_rolls_back(monkeypatch):
    db = _DB()
    authority = _authority()
    provider = _provider(authority)

    async def fail_stage(**_kwargs):
        raise TreatmentVitalUnavailable("TREATMENT_VITAL_STAGE_UNAVAILABLE")

    monkeypatch.setattr(routes, "stage_treatment_vital_write", fail_stage)

    with pytest.raises(HTTPException) as caught:
        await routes.write_treatment_vital(
            request=_request(_heart_rate_payload()),
            response=Response(),
            idempotency_key="vitals-route-0004",
            provider=provider,
            authority=authority,
            db=db,
        )

    assert caught.value.status_code == 503
    assert caught.value.detail == {
        "error_code": "TREATMENT_VITAL_STAGE_UNAVAILABLE"
    }
    assert db.commits == 0
    assert db.rollbacks == 1


@pytest.mark.asyncio
async def test_provider_trust_revoked_before_commit_rolls_back(monkeypatch):
    events = []
    db = _DB(events=events)
    authority = _authority()
    provider = _provider(authority)

    async def stage(**_kwargs):
        events.append("stage")
        return _result(authority)

    async def deny_final_trust(**_kwargs):
        events.append("final_trust")
        raise HTTPException(
            status_code=403,
            detail={"error_code": "CLINICAL_ELIGIBILITY_DENIED"},
        )

    monkeypatch.setattr(routes, "stage_treatment_vital_write", stage)
    monkeypatch.setattr(
        routes,
        "enforce_current_clinical_capability",
        deny_final_trust,
    )

    with pytest.raises(HTTPException) as caught:
        await routes.write_treatment_vital(
            request=_request(_heart_rate_payload()),
            response=Response(),
            idempotency_key="vitals-route-0005",
            provider=provider,
            authority=authority,
            db=db,
        )

    assert caught.value.status_code == 403
    assert caught.value.detail == {
        "error_code": "CLINICAL_ELIGIBILITY_DENIED"
    }
    assert events == ["stage", "final_trust", "rollback"]
    assert db.commits == 0


@pytest.mark.asyncio
async def test_final_provider_binding_mismatch_rolls_back(monkeypatch):
    db = _DB()
    authority = _authority()
    provider = _provider(authority)

    async def stage(**_kwargs):
        return _result(authority)

    async def mismatched_final_provider(**_kwargs):
        return _provider(authority, binding="different-provider-session")

    monkeypatch.setattr(routes, "stage_treatment_vital_write", stage)
    monkeypatch.setattr(
        routes,
        "enforce_current_clinical_capability",
        mismatched_final_provider,
    )

    with pytest.raises(HTTPException) as caught:
        await routes.write_treatment_vital(
            request=_request(_heart_rate_payload()),
            response=Response(),
            idempotency_key="vitals-route-0006",
            provider=provider,
            authority=authority,
            db=db,
        )

    assert caught.value.status_code == 403
    assert caught.value.detail == {
        "error_code": "TREATMENT_PROVIDER_SESSION_MISMATCH"
    }
    assert db.commits == 0
    assert db.rollbacks == 1


@pytest.mark.asyncio
async def test_commit_failure_rolls_back_and_returns_stable_error(monkeypatch):
    db = _DB(fail_commit=True)
    authority = _authority()
    provider = _provider(authority)

    async def stage(**_kwargs):
        return _result(authority)

    async def final_trust(**_kwargs):
        return provider

    monkeypatch.setattr(routes, "stage_treatment_vital_write", stage)
    monkeypatch.setattr(
        routes,
        "enforce_current_clinical_capability",
        final_trust,
    )

    with pytest.raises(HTTPException) as caught:
        await routes.write_treatment_vital(
            request=_request(_heart_rate_payload()),
            response=Response(),
            idempotency_key="vitals-route-0007",
            provider=provider,
            authority=authority,
            db=db,
        )

    assert caught.value.status_code == 503
    assert caught.value.detail == {
        "error_code": "TREATMENT_VITAL_COMMIT_UNAVAILABLE"
    }
    assert db.commits == 1
    assert db.rollbacks == 1
