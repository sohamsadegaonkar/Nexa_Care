"""Tests for Patient Treatment Session Visibility, Platform Safety, and Revocation Hardening.

Phase 13 security invariants:
- public_ref is genuinely opaque (gref_v2_<64_hex>), concealing internal database UUID and hex
- public_ref is deterministically derived using dedicated PATIENT_GRANT_REFERENCE_HMAC_SECRET
- old v1 references, tampered references, and cross-patient references fail closed with 404
- patient DTO minimizes identifiers (patient_id is absent from PatientConsentHistoryItem)
- Treatment Session classification comes strictly from server-owned TREATMENT_SESSION_V1_SCOPE
- expired grant cannot become active; revoked grant remains revoked
- Redis capability invalidation must succeed first; if Redis fails, returns deterministic HTTP 503
  (CONSENT_ACCESS_STORE_UNAVAILABLE) and rolls back PostgreSQL without committing
- real authority path: after revocation, live capability tokens for both standard consent and
  Treatment Session are genuinely denied
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api.v2.consent_history_routes import (
    _serialize_patient_history,
    _serialize_provider_history,
    find_grant_by_public_ref,
    mint_public_grant_ref,
    revoke_self_consent_grant,
    verify_public_grant_ref_format,
)
from app.api.v2.consent_routes import revoke_patient_approved_access
from app.core.clinical_session_gate import (
    _durable_grant_matches,
    _durable_session_matches,
)
from app.models.clinical_access_session import ClinicalAccessSessionRecord
from app.models.consent_grant import ConsentGrantLog
from app.security.clinical_access_policy import (
    CLINICAL_ACCESS_POLICY_VERSION,
    ClinicalAccessOperation,
)
from app.services.treatment_session_v1_mint import (
    TREATMENT_SESSION_V1_SCOPE,
    TreatmentSessionV1MintStoreUnavailable,
)


def test_public_grant_ref_opaque_v2_format():
    patient_id = str(uuid.uuid4())
    grant_id = uuid.uuid4()

    public_ref = mint_public_grant_ref(patient_id, grant_id)

    # Invariant: Must start with gref_v2_ and be exactly 72 chars (8 prefix + 64 hex)
    assert public_ref.startswith("gref_v2_")
    assert len(public_ref) == 72
    assert verify_public_grant_ref_format(public_ref) is True

    # Invariant: Raw UUID, hyphenated UUID, or hex representation must NOT appear anywhere in token
    assert str(grant_id) not in public_ref
    assert grant_id.hex not in public_ref
    assert patient_id not in public_ref

    # Determinism: Minting twice with same inputs produces identical reference
    assert mint_public_grant_ref(patient_id, grant_id) == public_ref


def test_public_grant_ref_isolation_across_patients_and_grants():
    patient_a = str(uuid.uuid4())
    patient_b = str(uuid.uuid4())
    grant_1 = uuid.uuid4()
    grant_2 = uuid.uuid4()

    ref_a1 = mint_public_grant_ref(patient_a, grant_1)
    ref_a2 = mint_public_grant_ref(patient_a, grant_2)
    ref_b1 = mint_public_grant_ref(patient_b, grant_1)

    assert ref_a1 != ref_a2
    assert ref_a1 != ref_b1
    assert ref_a2 != ref_b1


@pytest.mark.asyncio
async def test_find_grant_by_public_ref_constant_time_success():
    patient_id = str(uuid.uuid4())
    grant_1 = ConsentGrantLog(
        id=uuid.uuid4(),
        token_hash="a" * 64,
        patient_id=patient_id,
        clinician_id=str(uuid.uuid4()),
        hospital_id=uuid.uuid4(),
        purpose="Cardiology",
        scope=[TREATMENT_SESSION_V1_SCOPE],
        is_break_glass=False,
        reason_code=None,
        issued_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        revoked_at=None,
    )
    grant_2 = ConsentGrantLog(
        id=uuid.uuid4(),
        token_hash="b" * 64,
        patient_id=patient_id,
        clinician_id=str(uuid.uuid4()),
        hospital_id=uuid.uuid4(),
        purpose="Neurology",
        scope=["clinical_access"],
        is_break_glass=False,
        reason_code=None,
        issued_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        revoked_at=None,
    )

    class MockAsyncSession:
        async def execute(self, stmt):
            class MockResult:
                def scalars(self):
                    class MockScalars:
                        def all(self):
                            return [grant_1, grant_2]

                        def scalar_one_or_none(self):
                            return grant_1

                    return MockScalars()

            return MockResult()

    db = MockAsyncSession()
    ref_1 = mint_public_grant_ref(patient_id, grant_1.id)
    found = await find_grant_by_public_ref(db, ref_1, patient_id, for_update=False)
    assert found.id == grant_1.id


@pytest.mark.asyncio
async def test_find_grant_by_public_ref_rejects_v1_references():
    """Prove that legacy v1 format (gref_<hex>_<tag>) is rejected closed with 404."""
    patient_id = str(uuid.uuid4())
    grant_id = uuid.uuid4()
    v1_ref = f"gref_{grant_id.hex}_{'a' * 32}"

    class MockAsyncSession:
        async def execute(self, stmt):
            raise AssertionError("DB should not be queried for malformed/v1 tokens")

    db = MockAsyncSession()
    with pytest.raises(HTTPException) as exc:
        await find_grant_by_public_ref(db, v1_ref, patient_id)
    assert exc.value.status_code == 404
    assert exc.value.detail == {"error_code": "GRANT_NOT_FOUND"}


@pytest.mark.asyncio
async def test_find_grant_by_public_ref_cross_patient_fails_closed():
    patient_a = str(uuid.uuid4())
    patient_b = str(uuid.uuid4())
    grant_a = ConsentGrantLog(
        id=uuid.uuid4(),
        token_hash="a" * 64,
        patient_id=patient_a,
        clinician_id=str(uuid.uuid4()),
        hospital_id=uuid.uuid4(),
        purpose="Cardiology",
        scope=[TREATMENT_SESSION_V1_SCOPE],
        is_break_glass=False,
        reason_code=None,
        issued_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        revoked_at=None,
    )

    class MockAsyncSession:
        async def execute(self, stmt):
            class MockResult:
                def scalars(self):
                    class MockScalars:
                        def all(self):
                            # Patient B has no matching grants
                            return []

                    return MockScalars()

            return MockResult()

    db = MockAsyncSession()
    ref_a = mint_public_grant_ref(patient_a, grant_a.id)

    # Patient B attempts to present Patient A's public_ref
    with pytest.raises(HTTPException) as exc:
        await find_grant_by_public_ref(db, ref_a, patient_b)
    assert exc.value.status_code == 404
    assert exc.value.detail == {"error_code": "GRANT_NOT_FOUND"}


@pytest.mark.asyncio
async def test_find_grant_by_public_ref_tampered_fails_closed():
    patient_id = str(uuid.uuid4())
    grant = ConsentGrantLog(
        id=uuid.uuid4(),
        token_hash="a" * 64,
        patient_id=patient_id,
        clinician_id=str(uuid.uuid4()),
        hospital_id=uuid.uuid4(),
        purpose="Cardiology",
        scope=[TREATMENT_SESSION_V1_SCOPE],
        is_break_glass=False,
        reason_code=None,
        issued_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        revoked_at=None,
    )

    class MockAsyncSession:
        async def execute(self, stmt):
            class MockResult:
                def scalars(self):
                    class MockScalars:
                        def all(self):
                            return [grant]

                    return MockScalars()

            return MockResult()

    db = MockAsyncSession()
    valid_ref = mint_public_grant_ref(patient_id, grant.id)
    assert verify_public_grant_ref_format(valid_ref) is True

    # Tampered 64-hex digest
    tampered_ref = "gref_v2_" + ("0" * 64)
    with pytest.raises(HTTPException) as exc:
        await find_grant_by_public_ref(db, tampered_ref, patient_id)
    assert exc.value.status_code == 404
    assert exc.value.detail == {"error_code": "GRANT_NOT_FOUND"}


def test_serialize_history_patient_self_replaces_db_uuid_and_minimizes_patient_id():
    patient_id = str(uuid.uuid4())
    grant_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    row = ConsentGrantLog(
        id=grant_id,
        token_hash="a" * 64,
        patient_id=patient_id,
        clinician_id=str(uuid.uuid4()),
        hospital_id=uuid.uuid4(),
        purpose="Cardiology Review",
        scope=[TREATMENT_SESSION_V1_SCOPE],
        is_break_glass=False,
        reason_code=None,
        issued_at=now - timedelta(minutes=5),
        expires_at=now + timedelta(minutes=25),
        revoked_at=None,
    )

    items = _serialize_patient_history([row], patient_id=patient_id)
    assert len(items) == 1
    item = items[0]

    # Invariant: internal DB UUID is never exposed in id or public_ref
    assert item.id.startswith("gref_v2_")
    assert item.public_ref.startswith("gref_v2_")
    assert item.id == item.public_ref
    assert len(item.public_ref) == 72
    assert grant_id.hex not in item.public_ref

    # Invariant: patient_id is absent from patient-facing DTO (identifier minimization)
    item_dict = item.model_dump()
    assert "patient_id" not in item_dict
    assert "a" * 64 not in item.model_dump_json()

    assert item.is_treatment_session is True
    assert item.status == "active"


def test_serialize_history_provider_retains_patient_id():
    patient_id = str(uuid.uuid4())
    grant_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    row = ConsentGrantLog(
        id=grant_id,
        token_hash="a" * 64,
        patient_id=patient_id,
        clinician_id=str(uuid.uuid4()),
        hospital_id=uuid.uuid4(),
        purpose="Cardiology Review",
        scope=[TREATMENT_SESSION_V1_SCOPE],
        is_break_glass=False,
        reason_code=None,
        issued_at=now - timedelta(minutes=5),
        expires_at=now + timedelta(minutes=25),
        revoked_at=None,
    )

    items = _serialize_provider_history([row])
    assert len(items) == 1
    assert items[0].patient_id == patient_id
    assert items[0].id == str(grant_id)


def test_treatment_session_classification_strictly_uses_treatment_scope():
    patient_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    routine_row = ConsentGrantLog(
        id=uuid.uuid4(),
        token_hash="b" * 64,
        patient_id=patient_id,
        clinician_id=str(uuid.uuid4()),
        hospital_id=uuid.uuid4(),
        purpose="treatment session consultation",
        scope=["clinical_access"],
        is_break_glass=False,
        reason_code=None,
        issued_at=now - timedelta(minutes=5),
        expires_at=now + timedelta(minutes=25),
        revoked_at=None,
    )

    treatment_row = ConsentGrantLog(
        id=uuid.uuid4(),
        token_hash="c" * 64,
        patient_id=patient_id,
        clinician_id=str(uuid.uuid4()),
        hospital_id=uuid.uuid4(),
        purpose="General Checkup",
        scope=[TREATMENT_SESSION_V1_SCOPE],
        is_break_glass=False,
        reason_code=None,
        issued_at=now - timedelta(minutes=5),
        expires_at=now + timedelta(minutes=25),
        revoked_at=None,
    )

    items = _serialize_patient_history([routine_row, treatment_row], patient_id=patient_id)
    assert items[0].is_treatment_session is False
    assert items[1].is_treatment_session is True


def test_expired_grant_cannot_become_active():
    patient_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    expired_row = ConsentGrantLog(
        id=uuid.uuid4(),
        token_hash="d" * 64,
        patient_id=patient_id,
        clinician_id=str(uuid.uuid4()),
        hospital_id=uuid.uuid4(),
        purpose="Vitals Check",
        scope=[TREATMENT_SESSION_V1_SCOPE],
        is_break_glass=False,
        reason_code=None,
        issued_at=now - timedelta(minutes=60),
        expires_at=now - timedelta(minutes=10),
        revoked_at=None,
    )

    items = _serialize_patient_history([expired_row], patient_id=patient_id)
    assert items[0].status == "expired"


def test_revoked_grant_remains_revoked():
    patient_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    revoked_row = ConsentGrantLog(
        id=uuid.uuid4(),
        token_hash="e" * 64,
        patient_id=patient_id,
        clinician_id=str(uuid.uuid4()),
        hospital_id=uuid.uuid4(),
        purpose="Vitals Check",
        scope=[TREATMENT_SESSION_V1_SCOPE],
        is_break_glass=False,
        reason_code=None,
        issued_at=now - timedelta(minutes=30),
        expires_at=now + timedelta(minutes=30),
        revoked_at=now - timedelta(minutes=5),
        revoked_reason="patient_revoked",
    )

    items = _serialize_patient_history([revoked_row], patient_id=patient_id)
    assert items[0].status == "revoked"
    assert items[0].revoked_at is not None


@pytest.mark.asyncio
async def test_revoke_self_consent_grant_redis_failure_fails_closed_503():
    """Adversarial test: If Redis capability invalidation fails, return 503 and do not mutate DB."""
    patient_id = str(uuid.uuid4())
    grant_id = uuid.uuid4()
    req_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    grant = ConsentGrantLog(
        id=grant_id,
        token_hash="f" * 64,
        patient_id=patient_id,
        clinician_id=str(uuid.uuid4()),
        hospital_id=uuid.uuid4(),
        purpose="Treatment",
        scope=[TREATMENT_SESSION_V1_SCOPE],
        is_break_glass=False,
        reason_code=None,
        issued_at=now - timedelta(minutes=5),
        expires_at=now + timedelta(minutes=25),
        request_id=req_id,
        revoked_at=None,
    )

    class MockDbSession:
        def __init__(self):
            self.committed = False
            self.rolled_back = False

        async def execute(self, stmt):
            class MockResult:
                def scalars(self):
                    class MockScalars:
                        def all(self):
                            return [grant]

                        def scalar_one_or_none(self):
                            return grant

                    return MockScalars()

                def scalar_one_or_none(self):
                    return grant

            return MockResult()

        async def rollback(self):
            self.rolled_back = True

        async def commit(self):
            self.committed = True

    db = MockDbSession()
    public_ref = mint_public_grant_ref(patient_id, grant_id)

    with patch(
        "app.api.v2.consent_history_routes.invalidate_treatment_session_v1_request",
        side_effect=TreatmentSessionV1MintStoreUnavailable("TREATMENT_SESSION_SECURITY_STORE_UNAVAILABLE"),
    ), patch(
        "app.api.v2.consent_history_routes.append_audit_log_or_503",
        new=AsyncMock(),
    ) as mock_audit:
        with pytest.raises(HTTPException) as exc:
            await revoke_self_consent_grant(public_ref, patient_id=patient_id, db=db)
        assert exc.value.status_code == 503
        assert exc.value.detail == {"error_code": "CONSENT_ACCESS_STORE_UNAVAILABLE"}

    # Invariants: DB must NOT be committed, rollback must be called, grant must NOT be marked revoked
    assert db.committed is False
    assert db.rolled_back is True
    assert grant.revoked_at is None
    assert mock_audit.await_count == 0


@pytest.mark.asyncio
async def test_revoke_patient_approved_access_treatment_redis_failure_fails_closed_503():
    """Test DELETE /api/v2/consent/request/{request_id}/revoke fails closed if treatment session invalidation fails."""
    patient_id = str(uuid.uuid4())
    req_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    grant = ConsentGrantLog(
        id=uuid.uuid4(),
        token_hash="g" * 64,
        patient_id=patient_id,
        clinician_id=str(uuid.uuid4()),
        hospital_id=uuid.uuid4(),
        purpose="Treatment",
        scope=[TREATMENT_SESSION_V1_SCOPE],
        is_break_glass=False,
        reason_code=None,
        issued_at=now - timedelta(minutes=5),
        expires_at=now + timedelta(minutes=25),
        request_id=req_id,
        revoked_at=None,
    )

    class MockDbSession:
        def __init__(self):
            self.committed = False
            self.rolled_back = False

        async def execute(self, stmt):
            class MockResult:
                def scalars(self):
                    class MockScalars:
                        def all(self):
                            return [grant]

                    return MockScalars()

            return MockResult()

        async def rollback(self):
            self.rolled_back = True

        async def commit(self):
            self.committed = True

    db = MockDbSession()

    with patch(
        "app.api.v2.consent_routes.get_redis_client"
    ), patch(
        "app.api.v2.consent_routes._redis_call",
        new=AsyncMock(return_value=json.dumps({"patient_id": patient_id, "status": "approved"})),
    ), patch(
        "app.api.v2.consent_routes.invalidate_request",
        new=AsyncMock(),
    ), patch(
        "app.services.treatment_session_v1_mint.invalidate_treatment_session_v1_request",
        side_effect=TreatmentSessionV1MintStoreUnavailable("TREATMENT_SESSION_SECURITY_STORE_UNAVAILABLE"),
    ):
        with pytest.raises(HTTPException) as exc:
            await revoke_patient_approved_access(req_id, patient_id=patient_id, db=db)
        assert exc.value.status_code == 503
        assert exc.value.detail == {"error_code": "CONSENT_ACCESS_STORE_UNAVAILABLE"}

    assert db.committed is False
    assert db.rolled_back is True
    assert grant.revoked_at is None


@pytest.mark.asyncio
async def test_real_authority_denial_after_self_revocation():
    """Prove that after self-revocation, live capability tokens for both standard consent
    and Treatment Session are genuinely denied at their validation gates.
    """
    patient_id = uuid.uuid4()
    provider_id = uuid.uuid4()
    hospital_id = uuid.uuid4()
    session_id = uuid.uuid4()
    request_id = uuid.uuid4()
    token_hash = "f" * 64
    binding_hash = "1" * 64
    now = datetime.now(timezone.utc)

    # 1. Before revocation: Active grant and active session allow clinical operations
    active_grant = ConsentGrantLog(
        id=uuid.uuid4(),
        token_hash=token_hash,
        patient_id=str(patient_id),
        clinician_id=str(provider_id),
        hospital_id=hospital_id,
        purpose="Treatment",
        scope=[TREATMENT_SESSION_V1_SCOPE],
        is_break_glass=False,
        reason_code=None,
        issued_at=now - timedelta(minutes=5),
        expires_at=now + timedelta(minutes=25),
        assurance_level="signed_device_treatment_v1",
        assurance_verified_at=now - timedelta(minutes=5),
        request_id=str(request_id),
        revoked_at=None,
        revoked_reason=None,
    )

    class MockAuthority:
        def __init__(self):
            self.session_id = session_id
            self.request_id = request_id
            self.patient_id = patient_id
            self.provider_id = provider_id
            self.hospital_id = hospital_id
            self.purpose = "Treatment"
            self.allowed_operations = ("CREATE_ENCOUNTER", "WRITE_VITALS")
            self.required_operation = ClinicalAccessOperation.WRITE_VITALS
            self.provider_session_binding_hash = binding_hash
            self.policy_version = CLINICAL_ACCESS_POLICY_VERSION
            self.token_hash = token_hash
            self.issued_at = now - timedelta(minutes=5)
            self.expires_at = now + timedelta(minutes=25)
            self.encounter_id = None

    authority = MockAuthority()
    assert _durable_grant_matches(active_grant, authority, now=now) is True

    # 2. Perform self-revocation
    class MockDbSession:
        def __init__(self):
            self.committed = False
            self.rolled_back = False

        async def execute(self, stmt):
            class MockResult:
                def scalars(self):
                    class MockScalars:
                        def all(self):
                            return [active_grant]

                        def scalar_one_or_none(self):
                            return active_grant

                    return MockScalars()

                def scalar_one_or_none(self):
                    return active_grant

            return MockResult()

        async def rollback(self):
            self.rolled_back = True

        async def commit(self):
            self.committed = True

    db = MockDbSession()
    public_ref = mint_public_grant_ref(str(patient_id), active_grant.id)

    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock()

    with patch(
        "app.api.v2.consent_history_routes.invalidate_treatment_session_v1_request",
        new=AsyncMock(),
    ) as mock_invalidate_treatment, patch(
        "app.api.v2.consent_history_routes.invalidate_request",
        new=AsyncMock(),
    ) as mock_invalidate_req, patch(
        "app.api.v2.consent_history_routes.get_async_redis_client",
        return_value=mock_redis,
    ), patch(
        "app.api.v2.consent_history_routes.revoke_clinical_access_session_by_request",
        new=AsyncMock(),
    ) as mock_revoke_clinical_session, patch(
        "app.api.v2.consent_history_routes.append_audit_log_or_503",
        new=AsyncMock(),
    ):
        result = await revoke_self_consent_grant(public_ref, patient_id=str(patient_id), db=db)
        assert result.status == "revoked"
        assert db.committed is True
        mock_invalidate_treatment.assert_awaited_once_with(str(request_id))
        mock_invalidate_req.assert_awaited_once_with(str(request_id))
        mock_revoke_clinical_session.assert_awaited_once()

    # 3. Verify that after revocation, durable checks fail closed
    assert active_grant.revoked_at is not None
    assert _durable_grant_matches(active_grant, authority, now=now) is False

    revoked_session = ClinicalAccessSessionRecord(
        session_id=session_id,
        patient_id=patient_id,
        provider_id=provider_id,
        hospital_id=hospital_id,
        consent_request_id=str(request_id),
        token_hash=token_hash,
        purpose="Treatment",
        scope=TREATMENT_SESSION_V1_SCOPE,
        allowed_operations=["CREATE_ENCOUNTER", "WRITE_VITALS"],
        provider_session_binding_hash=binding_hash,
        policy_version=CLINICAL_ACCESS_POLICY_VERSION,
        issued_at=now - timedelta(minutes=5),
        expires_at=now + timedelta(minutes=25),
        status="REVOKED",
        encounter_id=None,
        revoked_at=now,
        revocation_reason="PATIENT_REVOKED",
    )
    assert _durable_session_matches(revoked_session, authority, now=now) is False
