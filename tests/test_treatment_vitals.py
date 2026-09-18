from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core import clinical_session_gate as gate
from app.core.clinical_session_gate import TreatmentSessionV1Authority
from app.models.patient_records import TimelineEvent, Vitals
from app.security.audit_context import AuditContext, AuditDomain
from app.security.clinical_access_policy import (
    CLINICAL_ACCESS_POLICY_VERSION,
    ClinicalAccessOperation,
)
from app.services import treatment_vitals as service


class _FirstResult:
    def __init__(self, row):
        self.row = row

    def first(self):
        return self.row


class _MutationDB:
    def __init__(self, *, existing=None, reserve=True):
        self.existing = existing
        self.reserve = reserve
        self.added = []
        self.executed = []
        self.flushed = 0

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.executed.append((sql, params))
        if "SELECT request_hash" in sql and "mutation_idempotency" in sql:
            return _FirstResult(self.existing)
        if "INSERT INTO public.mutation_idempotency" in sql:
            return _FirstResult(
                SimpleNamespace(id=uuid.uuid4()) if self.reserve else None
            )
        if "UPDATE public.mutation_idempotency" in sql:
            return _FirstResult(None)
        raise AssertionError(f"unexpected SQL: {sql}")

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        self.flushed += 1


class _ScalarResult:
    def __init__(self, row):
        self.row = row

    def scalar_one_or_none(self):
        return self.row


class _AuthorityDB:
    def __init__(self, *rows):
        self.rows = list(rows)

    async def execute(self, _statement):
        if not self.rows:
            raise AssertionError("unexpected database query")
        return _ScalarResult(self.rows.pop(0))


def _authority(
    *,
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
        provider_session_binding_hash="a" * 64,
        policy_version=CLINICAL_ACCESS_POLICY_VERSION,
        token_hash="b" * 64,
        issued_at=now - timedelta(seconds=5),
        expires_at=now + timedelta(minutes=10),
        encounter_id=uuid.uuid4(),
    )


def _audit_context(authority: TreatmentSessionV1Authority) -> AuditContext:
    return AuditContext.for_hospital(
        hospital_id=str(authority.hospital_id),
        domain=AuditDomain.PATIENT_RECORD,
    )


def _durable_rows(authority: TreatmentSessionV1Authority):
    now = datetime.now(timezone.utc)
    session = SimpleNamespace(
        session_id=authority.session_id,
        patient_id=authority.patient_id,
        provider_id=authority.provider_id,
        hospital_id=authority.hospital_id,
        consent_request_id=str(authority.request_id),
        token_hash=authority.token_hash,
        purpose=authority.purpose,
        scope="treatment",
        allowed_operations=list(authority.allowed_operations),
        provider_session_binding_hash=authority.provider_session_binding_hash,
        policy_version=authority.policy_version,
        issued_at=authority.issued_at,
        expires_at=authority.expires_at,
        status="ACTIVE",
        encounter_id=str(authority.encounter_id),
        revoked_at=None,
        revocation_reason=None,
    )
    grant = SimpleNamespace(
        token_hash=authority.token_hash,
        patient_id=str(authority.patient_id),
        clinician_id=str(authority.provider_id),
        hospital_id=authority.hospital_id,
        purpose=authority.purpose,
        scope=["treatment"],
        is_break_glass=False,
        reason_code=None,
        issued_at=now,
        expires_at=authority.expires_at,
        revoked_at=None,
        revoked_reason=None,
        assurance_level="signed_device_treatment_v1",
        assurance_verified_at=now,
        request_id=str(authority.request_id),
    )
    encounter = SimpleNamespace(
        encounter_id=authority.encounter_id,
        clinical_session_id=authority.session_id,
        patient_id=authority.patient_id,
        provider_id=authority.provider_id,
        hospital_id=authority.hospital_id,
    )
    return session, grant, encounter


def test_typed_observation_factories_are_server_normalized():
    now = datetime.now(timezone.utc)
    bp = service.blood_pressure_observation(
        systolic_bp=120,
        diastolic_bp=80,
        recorded_at=now,
    )
    hr = service.heart_rate_observation(beats_per_minute=72, recorded_at=now)
    temp = service.temperature_observation(celsius="37.00", recorded_at=now)
    spo2 = service.spo2_observation(percentage="98.0", recorded_at=now)

    assert (bp.vital_type.value, bp.value, bp.unit) == ("BP", "120/80", "mmHg")
    assert (hr.vital_type.value, hr.value, hr.unit) == ("HR", "72", "bpm")
    assert (temp.vital_type.value, temp.value, temp.unit) == ("temp", "37", "C")
    assert (spo2.vital_type.value, spo2.value, spo2.unit) == ("SpO2", "98", "%")


@pytest.mark.parametrize(
    ("factory", "kwargs"),
    [
        (
            service.blood_pressure_observation,
            {"systolic_bp": 0, "diastolic_bp": 80},
        ),
        (
            service.heart_rate_observation,
            {"beats_per_minute": True},
        ),
        (
            service.temperature_observation,
            {"celsius": "NaN"},
        ),
        (
            service.spo2_observation,
            {"percentage": "100.1"},
        ),
    ],
)
def test_typed_observation_factories_reject_malformed_values(factory, kwargs):
    with pytest.raises(service.TreatmentVitalValidationError):
        factory(recorded_at=datetime.now(timezone.utc), **kwargs)


def test_typed_observation_requires_timezone_aware_recorded_at():
    with pytest.raises(service.TreatmentVitalValidationError) as caught:
        service.heart_rate_observation(
            beats_per_minute=72,
            recorded_at=datetime.now(),
        )
    assert caught.value.code == "TREATMENT_VITAL_RECORDED_AT_INVALID"


def test_service_rejects_direct_noncanonical_observation_construction():
    bad = service.TreatmentVitalObservation(
        vital_type=service.TreatmentVitalType.HEART_RATE,
        value="072",
        unit="bpm",
        recorded_at=datetime.now(timezone.utc),
    )

    with pytest.raises(service.TreatmentVitalValidationError) as caught:
        service._validate_normalized_observation(bad)

    assert caught.value.code == "TREATMENT_VITAL_VALUE_NOT_CANONICAL"


def test_idempotency_hash_binds_every_authority_and_observation_semantic():
    authority = _authority()
    observation = service.heart_rate_observation(
        beats_per_minute=72,
        recorded_at=datetime.now(timezone.utc),
    )
    baseline = service._canonical_request_hash(
        authority=authority,
        observation=observation,
    )

    authority_variants = (
        replace(authority, session_id=uuid.uuid4()),
        replace(authority, encounter_id=uuid.uuid4()),
        replace(authority, patient_id=uuid.uuid4()),
        replace(authority, provider_id=uuid.uuid4()),
        replace(authority, hospital_id=uuid.uuid4()),
    )
    for changed in authority_variants:
        assert service._canonical_request_hash(
            authority=changed,
            observation=observation,
        ) != baseline

    observation_variants = (
        service.heart_rate_observation(
            beats_per_minute=73,
            recorded_at=observation.recorded_at,
        ),
        service.blood_pressure_observation(
            systolic_bp=120,
            diastolic_bp=80,
            recorded_at=observation.recorded_at,
        ),
        replace(observation, unit="beats/min"),
        replace(
            observation,
            recorded_at=observation.recorded_at + timedelta(seconds=1),
        ),
    )
    for changed in observation_variants:
        assert service._canonical_request_hash(
            authority=authority,
            observation=changed,
        ) != baseline


@pytest.mark.asyncio
async def test_write_authority_lock_requires_authority_encounter_before_query():
    authority = replace(_authority(), encounter_id=None)

    with pytest.raises(gate.TreatmentSessionV1GateDenied) as caught:
        await gate.lock_treatment_write_authority(
            db=_AuthorityDB(),
            authority=authority,
            required_operation=ClinicalAccessOperation.WRITE_VITALS,
        )

    assert caught.value.code == "TREATMENT_ENCOUNTER_REQUIRED"


@pytest.mark.asyncio
async def test_write_authority_lock_rejects_durable_session_encounter_mismatch():
    authority = _authority()
    session, _grant, _encounter = _durable_rows(authority)
    session.encounter_id = str(uuid.uuid4())

    with pytest.raises(gate.TreatmentSessionV1GateDenied) as caught:
        await gate.lock_treatment_write_authority(
            db=_AuthorityDB(session),
            authority=authority,
            required_operation=ClinicalAccessOperation.WRITE_VITALS,
        )

    assert caught.value.code == "TREATMENT_ENCOUNTER_NOT_AUTHORIZED"


@pytest.mark.asyncio
async def test_write_authority_lock_rejects_missing_canonical_encounter_row():
    authority = _authority()
    session, grant, _encounter = _durable_rows(authority)

    with pytest.raises(gate.TreatmentSessionV1GateDenied) as caught:
        await gate.lock_treatment_write_authority(
            db=_AuthorityDB(session, grant, None),
            authority=authority,
            required_operation=ClinicalAccessOperation.WRITE_VITALS,
        )

    assert caught.value.code == "TREATMENT_ENCOUNTER_REQUIRED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "expected_code"),
    [
        ("clinical_session_id", "TREATMENT_ENCOUNTER_NOT_AUTHORIZED"),
        ("patient_id", "TREATMENT_ENCOUNTER_NOT_AUTHORIZED"),
        ("provider_id", "TREATMENT_ENCOUNTER_NOT_AUTHORIZED"),
        ("hospital_id", "TREATMENT_ENCOUNTER_NOT_AUTHORIZED"),
    ],
)
async def test_write_authority_lock_rejects_canonical_encounter_binding_mismatch(
    field,
    expected_code,
):
    authority = _authority()
    session, grant, encounter = _durable_rows(authority)
    setattr(encounter, field, uuid.uuid4())

    with pytest.raises(gate.TreatmentSessionV1GateDenied) as caught:
        await gate.lock_treatment_write_authority(
            db=_AuthorityDB(session, grant, encounter),
            authority=authority,
            required_operation=ClinicalAccessOperation.WRITE_VITALS,
        )

    assert caught.value.code == expected_code


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        "patient_id",
        "provider_id",
        "hospital_id",
        "consent_request_id",
        "token_hash",
        "provider_session_binding_hash",
        "allowed_operations",
    ],
)
async def test_write_authority_lock_rejects_durable_session_disagreement(field):
    authority = _authority()
    session, _grant, _encounter = _durable_rows(authority)
    replacements = {
        "patient_id": uuid.uuid4(),
        "provider_id": uuid.uuid4(),
        "hospital_id": uuid.uuid4(),
        "consent_request_id": str(uuid.uuid4()),
        "token_hash": "c" * 64,
        "provider_session_binding_hash": "d" * 64,
        "allowed_operations": [ClinicalAccessOperation.READ_DOCUMENTS.value],
    }
    setattr(session, field, replacements[field])

    with pytest.raises(gate.TreatmentSessionV1GateDenied) as caught:
        await gate.lock_treatment_write_authority(
            db=_AuthorityDB(session),
            authority=authority,
            required_operation=ClinicalAccessOperation.WRITE_VITALS,
        )

    assert caught.value.code == "TREATMENT_SESSION_NOT_AUTHORIZED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        "patient_id",
        "clinician_id",
        "hospital_id",
        "request_id",
        "token_hash",
        "scope",
        "revoked_at",
    ],
)
async def test_write_authority_lock_rejects_durable_grant_disagreement(field):
    authority = _authority()
    session, grant, _encounter = _durable_rows(authority)
    replacements = {
        "patient_id": str(uuid.uuid4()),
        "clinician_id": str(uuid.uuid4()),
        "hospital_id": uuid.uuid4(),
        "request_id": str(uuid.uuid4()),
        "token_hash": "c" * 64,
        "scope": ["clinical"],
        "revoked_at": datetime.now(timezone.utc),
    }
    setattr(grant, field, replacements[field])

    with pytest.raises(gate.TreatmentSessionV1GateDenied) as caught:
        await gate.lock_treatment_write_authority(
            db=_AuthorityDB(session, grant),
            authority=authority,
            required_operation=ClinicalAccessOperation.WRITE_VITALS,
        )

    assert caught.value.code == "TREATMENT_SESSION_NOT_AUTHORIZED"


@pytest.mark.asyncio
async def test_stage_treatment_vital_uses_only_server_authority(monkeypatch):
    authority = _authority()
    encounter = SimpleNamespace(encounter_id=authority.encounter_id)
    lock = AsyncMock(return_value=encounter)
    audit = AsyncMock()
    monkeypatch.setattr(service, "lock_treatment_write_authority", lock)
    monkeypatch.setattr(service, "enqueue_audit_event", audit)
    db = _MutationDB()
    observation = service.heart_rate_observation(
        beats_per_minute=72,
        recorded_at=datetime.now(timezone.utc),
    )

    result = await service.stage_treatment_vital_write(
        db=db,
        authority=authority,
        observation=observation,
        idempotency_key="vitals-write-0001",
        audit_context=_audit_context(authority),
    )

    assert result.idempotent_replay is False
    assert result.encounter_id == authority.encounter_id
    assert result.vital_type is service.TreatmentVitalType.HEART_RATE

    vitals = [row for row in db.added if isinstance(row, Vitals)]
    timeline = [row for row in db.added if isinstance(row, TimelineEvent)]
    assert len(vitals) == 1
    assert len(timeline) == 1

    vital = vitals[0]
    assert vital.patient_id == authority.patient_id
    assert vital.encounter_id == authority.encounter_id
    assert vital.type == "HR"
    assert vital.value == "72"
    assert vital.unit == "bpm"
    assert vital.source == "manual"
    assert vital.confidence is None
    assert vital.source_document_id is None

    assert timeline[0].patient_id == authority.patient_id
    assert timeline[0].event_ref_id == vital.id
    assert timeline[0].source == "manual"
    assert "72 bpm" in timeline[0].summary

    lock.assert_awaited_once_with(
        db=db,
        authority=authority,
        required_operation=ClinicalAccessOperation.WRITE_VITALS,
    )
    kwargs = audit.await_args.kwargs
    assert kwargs["event_type"] == "PATIENT_RECORD_APPEND_SUCCESS"
    assert kwargs["patient_id"] == str(authority.patient_id)
    expected_metadata = {
        "clinical_session_id": str(authority.session_id),
        "encounter_id": str(authority.encounter_id),
        "operation": ClinicalAccessOperation.WRITE_VITALS.value,
        "record_type": "vitals",
    }
    assert kwargs["metadata"] == expected_metadata
    assert set(kwargs["metadata"]) == {
        "clinical_session_id",
        "encounter_id",
        "operation",
        "record_type",
    }
    for forbidden_key in {
        "value",
        "unit",
        "systolic_bp",
        "diastolic_bp",
        "heart_rate",
        "temperature",
        "spo2",
        "summary",
        "request",
        "raw_request",
        "payload",
        "token",
    }:
        assert forbidden_key not in kwargs["metadata"]


@pytest.mark.asyncio
async def test_stage_treatment_vital_rejects_wrong_operation_before_write(monkeypatch):
    authority = _authority(operation=ClinicalAccessOperation.WRITE_PRESCRIPTION)
    lock = AsyncMock()
    monkeypatch.setattr(service, "lock_treatment_write_authority", lock)

    with pytest.raises(gate.TreatmentSessionV1GateDenied) as caught:
        await service.stage_treatment_vital_write(
            db=_MutationDB(),
            authority=authority,
            observation=service.heart_rate_observation(
                beats_per_minute=72,
                recorded_at=datetime.now(timezone.utc),
            ),
            idempotency_key="vitals-write-0002",
            audit_context=_audit_context(authority),
        )

    assert caught.value.code == "TREATMENT_OPERATION_NOT_AUTHORIZED"
    lock.assert_not_awaited()


@pytest.mark.asyncio
async def test_stage_treatment_vital_rejects_wrong_audit_partition(monkeypatch):
    authority = _authority()
    lock = AsyncMock()
    monkeypatch.setattr(service, "lock_treatment_write_authority", lock)

    with pytest.raises(gate.TreatmentSessionV1GateDenied) as caught:
        await service.stage_treatment_vital_write(
            db=_MutationDB(),
            authority=authority,
            observation=service.heart_rate_observation(
                beats_per_minute=72,
                recorded_at=datetime.now(timezone.utc),
            ),
            idempotency_key="vitals-write-0003",
            audit_context=AuditContext.for_hospital(
                hospital_id=str(uuid.uuid4()),
                domain=AuditDomain.PATIENT_RECORD,
            ),
        )

    assert caught.value.code == "TREATMENT_AUDIT_CONTEXT_MISMATCH"
    lock.assert_not_awaited()


@pytest.mark.asyncio
async def test_stage_treatment_vital_replays_same_completed_request(monkeypatch):
    authority = _authority()
    observation = service.heart_rate_observation(
        beats_per_minute=72,
        recorded_at=datetime.now(timezone.utc),
    )
    request_hash = service._canonical_request_hash(
        authority=authority,
        observation=observation,
    )
    record_id = uuid.uuid4()
    existing = SimpleNamespace(
        request_hash=request_hash,
        response_status=200,
        response_payload={
            "record_id": str(record_id),
            "encounter_id": str(authority.encounter_id),
            "vital_type": "HR",
            "recorded_at": observation.recorded_at.isoformat(),
            "status": "committed",
        },
    )
    monkeypatch.setattr(
        service,
        "lock_treatment_write_authority",
        AsyncMock(return_value=SimpleNamespace(encounter_id=authority.encounter_id)),
    )
    audit = AsyncMock()
    monkeypatch.setattr(service, "enqueue_audit_event", audit)

    result = await service.stage_treatment_vital_write(
        db=_MutationDB(existing=existing),
        authority=authority,
        observation=observation,
        idempotency_key="vitals-write-0004",
        audit_context=_audit_context(authority),
    )

    assert result.idempotent_replay is True
    assert result.record_id == record_id
    audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_stage_treatment_vital_rejects_idempotency_key_semantic_reuse(monkeypatch):
    authority = _authority()
    monkeypatch.setattr(
        service,
        "lock_treatment_write_authority",
        AsyncMock(return_value=SimpleNamespace(encounter_id=authority.encounter_id)),
    )
    existing = SimpleNamespace(
        request_hash="f" * 64,
        response_status=200,
        response_payload={},
    )

    with pytest.raises(service.TreatmentVitalIdempotencyConflict) as caught:
        await service.stage_treatment_vital_write(
            db=_MutationDB(existing=existing),
            authority=authority,
            observation=service.heart_rate_observation(
                beats_per_minute=72,
                recorded_at=datetime.now(timezone.utc),
            ),
            idempotency_key="vitals-write-0005",
            audit_context=_audit_context(authority),
        )

    assert caught.value.code == "TREATMENT_VITAL_IDEMPOTENCY_KEY_REUSED"


@pytest.mark.asyncio
async def test_audit_stage_failure_fails_write_staging(monkeypatch):
    authority = _authority()
    monkeypatch.setattr(
        service,
        "lock_treatment_write_authority",
        AsyncMock(return_value=SimpleNamespace(encounter_id=authority.encounter_id)),
    )
    monkeypatch.setattr(
        service,
        "enqueue_audit_event",
        AsyncMock(side_effect=RuntimeError("audit unavailable")),
    )
    db = _MutationDB()

    with pytest.raises(service.TreatmentVitalUnavailable) as caught:
        await service.stage_treatment_vital_write(
            db=db,
            authority=authority,
            observation=service.heart_rate_observation(
                beats_per_minute=72,
                recorded_at=datetime.now(timezone.utc),
            ),
            idempotency_key="vitals-write-0006",
            audit_context=_audit_context(authority),
        )

    assert caught.value.code == "TREATMENT_VITAL_STAGE_UNAVAILABLE"
    assert not any(
        "UPDATE public.mutation_idempotency" in sql for sql, _params in db.executed
    )


@pytest.mark.asyncio
async def test_write_authority_lock_requires_exact_canonical_encounter():
    authority = _authority()
    now = datetime.now(timezone.utc)
    session = SimpleNamespace(
        session_id=authority.session_id,
        patient_id=authority.patient_id,
        provider_id=authority.provider_id,
        hospital_id=authority.hospital_id,
        consent_request_id=str(authority.request_id),
        token_hash=authority.token_hash,
        purpose=authority.purpose,
        scope="treatment",
        allowed_operations=list(authority.allowed_operations),
        provider_session_binding_hash=authority.provider_session_binding_hash,
        policy_version=authority.policy_version,
        issued_at=authority.issued_at,
        expires_at=authority.expires_at,
        status="ACTIVE",
        encounter_id=str(authority.encounter_id),
        revoked_at=None,
        revocation_reason=None,
    )
    grant = SimpleNamespace(
        token_hash=authority.token_hash,
        patient_id=str(authority.patient_id),
        clinician_id=str(authority.provider_id),
        hospital_id=authority.hospital_id,
        purpose=authority.purpose,
        scope=["treatment"],
        is_break_glass=False,
        reason_code=None,
        issued_at=now,
        expires_at=authority.expires_at,
        revoked_at=None,
        revoked_reason=None,
        assurance_level="signed_device_treatment_v1",
        assurance_verified_at=now,
        request_id=str(authority.request_id),
    )
    encounter = SimpleNamespace(
        encounter_id=authority.encounter_id,
        clinical_session_id=authority.session_id,
        patient_id=authority.patient_id,
        provider_id=authority.provider_id,
        hospital_id=authority.hospital_id,
    )

    locked = await gate.lock_treatment_write_authority(
        db=_AuthorityDB(session, grant, encounter),
        authority=authority,
        required_operation=ClinicalAccessOperation.WRITE_VITALS,
    )
    assert locked is encounter


@pytest.mark.asyncio
async def test_write_authority_lock_denies_cross_patient_encounter():
    authority = _authority()
    now = datetime.now(timezone.utc)
    session = SimpleNamespace(
        session_id=authority.session_id,
        patient_id=authority.patient_id,
        provider_id=authority.provider_id,
        hospital_id=authority.hospital_id,
        consent_request_id=str(authority.request_id),
        token_hash=authority.token_hash,
        purpose=authority.purpose,
        scope="treatment",
        allowed_operations=list(authority.allowed_operations),
        provider_session_binding_hash=authority.provider_session_binding_hash,
        policy_version=authority.policy_version,
        issued_at=authority.issued_at,
        expires_at=authority.expires_at,
        status="ACTIVE",
        encounter_id=str(authority.encounter_id),
        revoked_at=None,
        revocation_reason=None,
    )
    grant = SimpleNamespace(
        token_hash=authority.token_hash,
        patient_id=str(authority.patient_id),
        clinician_id=str(authority.provider_id),
        hospital_id=authority.hospital_id,
        purpose=authority.purpose,
        scope=["treatment"],
        is_break_glass=False,
        reason_code=None,
        issued_at=now,
        expires_at=authority.expires_at,
        revoked_at=None,
        revoked_reason=None,
        assurance_level="signed_device_treatment_v1",
        assurance_verified_at=now,
        request_id=str(authority.request_id),
    )
    encounter = SimpleNamespace(
        encounter_id=authority.encounter_id,
        clinical_session_id=authority.session_id,
        patient_id=uuid.uuid4(),
        provider_id=authority.provider_id,
        hospital_id=authority.hospital_id,
    )

    with pytest.raises(gate.TreatmentSessionV1GateDenied) as caught:
        await gate.lock_treatment_write_authority(
            db=_AuthorityDB(session, grant, encounter),
            authority=authority,
            required_operation=ClinicalAccessOperation.WRITE_VITALS,
        )

    assert caught.value.code == "TREATMENT_ENCOUNTER_NOT_AUTHORIZED"
