from __future__ import annotations

import uuid

import pytest

from app.services.policy_service import (
    PolicyIdempotencyKeyReused,
    PolicyService,
    PolicyValidationError,
    PolicyVersionConflict,
    validate_idempotency_key,
)


class _Result:
    def __init__(self, row=None):
        self._row = row

    def first(self):
        return self._row


class _FakeRow:
    def __init__(self, patient_uuid, consent_assurance_policy, version, last_idempotency_key=None):
        self.patient_uuid = patient_uuid
        self.consent_assurance_policy = consent_assurance_policy
        self.version = version
        self.last_idempotency_key = last_idempotency_key


class _FakeDB:
    """Minimal AsyncSession stand-in with an in-memory policy row and an
    outbox list, faithful enough to exercise set_policy_atomic's real logic
    (CAS match/mismatch, first-insert, outbox insert ordering)."""

    def __init__(self, existing: _FakeRow | None):
        self.row = existing
        self.outbox: list[dict] = []
        self.commits = 0
        self.rollbacks = 0

    async def get(self, model, patient_uuid):
        return self.row

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "UPDATE patient_policies" in sql:
            if self.row is None or self.row.version != params["expected_version"]:
                return _Result(None)
            self.row.consent_assurance_policy = params["new_policy"]
            self.row.version += 1
            self.row.last_idempotency_key = params["idempotency_key"]
            return _Result((self.row.version, self.row.consent_assurance_policy))
        if "INSERT INTO public.audit_outbox" in sql:
            self.outbox.append(dict(params))
            return _Result()
        if "INSERT INTO patient_policies" in sql:
            if self.row is not None:
                return _Result(None)  # ON CONFLICT DO NOTHING: row already exists
            self.row = _FakeRow(params["patient_uuid"], params["new_policy"], 1, params["idempotency_key"])
            return _Result((1, params["new_policy"]))
        raise AssertionError(f"unexpected SQL in fake DB: {sql}")

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


def test_validate_idempotency_key_rejects_short_and_malformed():
    with pytest.raises(PolicyValidationError):
        validate_idempotency_key("short")
    with pytest.raises(PolicyValidationError):
        validate_idempotency_key("has spaces!!" * 5)
    assert validate_idempotency_key("valid-key-001") == "valid-key-001"


@pytest.mark.asyncio
async def test_first_ever_policy_write_inserts_at_version_one():
    patient_uuid = uuid.uuid4()
    db = _FakeDB(existing=None)
    service = PolicyService(db)

    result = await service.set_policy_atomic(
        patient_uuid, "push_approved",
        expected_version=0, idempotency_key="req-first-write-001",
        actor_id="doctor-1", tenant_id="hosp-1",
    )

    assert result.version == 1
    assert result.consent_assurance_policy == "push_approved"
    assert result.idempotent_replay is False
    assert db.commits == 1
    assert len(db.outbox) == 1
    assert db.outbox[0]["idempotency_key"] == "req-first-write-001"


@pytest.mark.asyncio
async def test_cas_update_with_correct_expected_version_succeeds():
    patient_uuid = uuid.uuid4()
    db = _FakeDB(existing=_FakeRow(patient_uuid, "standard", 3))
    service = PolicyService(db)

    result = await service.set_policy_atomic(
        patient_uuid, "biometric_confirmed",
        expected_version=3, idempotency_key="req-cas-002",
        actor_id="doctor-1", tenant_id="hosp-1",
    )

    assert result.version == 4
    assert result.consent_assurance_policy == "biometric_confirmed"
    assert len(db.outbox) == 1
    outbox_payload = db.outbox[0]["payload"]
    assert '"old_policy":"standard"' in outbox_payload
    assert '"new_policy":"biometric_confirmed"' in outbox_payload


@pytest.mark.asyncio
async def test_cas_update_with_stale_expected_version_conflicts():
    patient_uuid = uuid.uuid4()
    db = _FakeDB(existing=_FakeRow(patient_uuid, "standard", 5))
    service = PolicyService(db)

    with pytest.raises(PolicyVersionConflict):
        await service.set_policy_atomic(
            patient_uuid, "push_approved",
            expected_version=3,  # stale -- real version is 5
            idempotency_key="req-stale-003",
            actor_id="doctor-1", tenant_id="hosp-1",
        )

    assert db.rollbacks == 1
    assert len(db.outbox) == 0  # no audit event for a rejected mutation


@pytest.mark.asyncio
async def test_two_concurrent_updates_only_one_succeeds():
    """Simulates two requests racing on the same expected_version; only the
    first CAS should succeed, the second must see a version conflict."""
    patient_uuid = uuid.uuid4()
    db = _FakeDB(existing=_FakeRow(patient_uuid, "standard", 1))
    service = PolicyService(db)

    first = await service.set_policy_atomic(
        patient_uuid, "push_approved",
        expected_version=1, idempotency_key="req-race-a",
        actor_id="doctor-a", tenant_id="hosp-1",
    )
    assert first.version == 2

    with pytest.raises(PolicyVersionConflict):
        await service.set_policy_atomic(
            patient_uuid, "biometric_confirmed",
            expected_version=1,  # both requests read version=1 before either wrote
            idempotency_key="req-race-b",
            actor_id="doctor-b", tenant_id="hosp-1",
        )


@pytest.mark.asyncio
async def test_same_idempotency_key_same_payload_replays_without_new_outbox_event():
    patient_uuid = uuid.uuid4()
    db = _FakeDB(existing=_FakeRow(patient_uuid, "push_approved", 2, last_idempotency_key="req-replay-004"))
    service = PolicyService(db)

    result = await service.set_policy_atomic(
        patient_uuid, "push_approved",  # same payload as what that key already produced
        expected_version=2, idempotency_key="req-replay-004",
        actor_id="doctor-1", tenant_id="hosp-1",
    )

    assert result.idempotent_replay is True
    assert result.version == 2
    assert len(db.outbox) == 0  # replay must not create a second audit event
    assert db.commits == 0


@pytest.mark.asyncio
async def test_same_idempotency_key_different_payload_is_rejected():
    patient_uuid = uuid.uuid4()
    db = _FakeDB(existing=_FakeRow(patient_uuid, "push_approved", 2, last_idempotency_key="req-reuse-005"))
    service = PolicyService(db)

    with pytest.raises(PolicyIdempotencyKeyReused):
        await service.set_policy_atomic(
            patient_uuid, "biometric_confirmed",  # different payload, same key -- must fail
            expected_version=2, idempotency_key="req-reuse-005",
            actor_id="doctor-1", tenant_id="hosp-1",
        )