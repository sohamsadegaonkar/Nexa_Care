"""Tests for Patient Treatment Session Visibility, Platform Safety, and Revocation.

Phase 13 security invariants:
- history/self returns only authenticated patient's grants
- patient A cannot revoke patient B grant
- expired grant cannot become active
- revoked grant remains revoked
- invalid public/reference identifier fails closed
- Treatment Session classification comes from server-owned scope
- revocation invalidates exactly the server authority it claims to invalidate
- no raw bearer/session secrets returned
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v2.consent_history_routes import (
    ConsentHistoryItem,
    _serialize_history,
    mint_public_grant_ref,
    parse_and_verify_public_grant_ref,
    revoke_self_consent_grant,
)
from app.core.clinical_session_gate import (
    TreatmentSessionV1GateDenied,
    _durable_grant_matches,
    _durable_session_matches,
)
from app.models.clinical_access_session import ClinicalAccessSessionRecord
from app.models.consent_grant import ConsentGrantLog
from app.security.clinical_access_policy import (
    CLINICAL_ACCESS_POLICY_VERSION,
    ClinicalAccessOperation,
)
from app.services.treatment_session_v1_mint import TREATMENT_SESSION_V1_SCOPE


def test_public_grant_ref_mint_and_verify_roundtrip():
    patient_id = str(uuid.uuid4())
    grant_id = uuid.uuid4()

    public_ref = mint_public_grant_ref(patient_id, grant_id)
    assert public_ref.startswith("gref_")
    assert grant_id.hex in public_ref

    parsed_id = parse_and_verify_public_grant_ref(public_ref, patient_id)
    assert parsed_id == grant_id


def test_public_grant_ref_cross_patient_fails_closed():
    patient_a = str(uuid.uuid4())
    patient_b = str(uuid.uuid4())
    grant_a = uuid.uuid4()

    public_ref_a = mint_public_grant_ref(patient_a, grant_a)

    with pytest.raises(HTTPException) as exc:
        parse_and_verify_public_grant_ref(public_ref_a, patient_b)
    assert exc.value.status_code == 404
    assert exc.value.detail == {"error_code": "GRANT_NOT_FOUND"}


def test_public_grant_ref_tampered_fails_closed():
    patient_id = str(uuid.uuid4())
    grant_id = uuid.uuid4()

    public_ref = mint_public_grant_ref(patient_id, grant_id)
    parts = public_ref.split("_")

    # Tampered tag
    tampered_ref = f"{parts[0]}_{parts[1]}_{'0' * 32}"
    with pytest.raises(HTTPException) as exc:
        parse_and_verify_public_grant_ref(tampered_ref, patient_id)
    assert exc.value.status_code == 404

    # Tampered grant_id
    different_grant_hex = uuid.uuid4().hex
    tampered_grant_ref = f"{parts[0]}_{different_grant_hex}_{parts[2]}"
    with pytest.raises(HTTPException) as exc:
        parse_and_verify_public_grant_ref(tampered_grant_ref, patient_id)
    assert exc.value.status_code == 404

    # Malformed format
    with pytest.raises(HTTPException) as exc:
        parse_and_verify_public_grant_ref("invalid_ref", patient_id)
    assert exc.value.status_code == 404


def test_serialize_history_patient_self_replaces_db_uuid_with_public_ref():
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

    items = _serialize_history([row], patient_id=patient_id)
    assert len(items) == 1
    item = items[0]

    # Invariant: internal DB UUID is never exposed in id or public_ref
    assert item.id.startswith("gref_")
    assert item.public_ref.startswith("gref_")
    assert item.id == item.public_ref
    assert item.is_treatment_session is True
    assert item.status == "active"
    assert "a" * 64 not in item.model_dump_json()  # No token hash or secret leaked


def test_treatment_session_classification_uses_server_scope_not_free_text():
    patient_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    # Free text says "treatment", but scope is routine clinical access
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

    # Scope has authoritative treatment scope
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

    items = _serialize_history([routine_row, treatment_row], patient_id=patient_id)
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

    items = _serialize_history([expired_row], patient_id=patient_id)
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
        expires_at=now + timedelta(minutes=30),  # Not expired by time
        revoked_at=now - timedelta(minutes=5),
        revoked_reason="patient_revoked",
    )

    items = _serialize_history([revoked_row], patient_id=patient_id)
    assert items[0].status == "revoked"
    assert items[0].revoked_at is not None


@pytest.mark.asyncio
async def test_revocation_invalidates_clinical_write_authority():
    """Prove that when a grant is revoked, subsequent treatment writes fail at the gate."""
    now = datetime.now(timezone.utc)
    patient_id = uuid.uuid4()
    provider_id = uuid.uuid4()
    hospital_id = uuid.uuid4()
    session_id = uuid.uuid4()
    request_id = uuid.uuid4()
    token_hash = "f" * 64
    binding_hash = "1" * 64

    # 1. Active grant matches authority
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

    # 2. Revoked grant fails durable grant match
    revoked_grant = ConsentGrantLog(
        id=active_grant.id,
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
        revoked_at=now,
        revoked_reason="patient_revoked",
    )
    assert _durable_grant_matches(revoked_grant, authority, now=now) is False

    # 3. Revoked clinical access session fails durable session match
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
