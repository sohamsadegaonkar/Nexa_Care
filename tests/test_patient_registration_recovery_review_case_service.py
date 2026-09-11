"""Focused contracts for durable registration-recovery review case creation."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.services.patient_registration_recovery_review_service as review_service
from app.models.patient_registration_recovery_review import (
    REGISTRATION_RECOVERY_REVIEW_CONTRACT_VERSION,
    REGISTRATION_RECOVERY_REVIEW_POLICY_VERSION,
    PatientRegistrationRecoveryReviewCase,
    RegistrationRecoveryReviewReason,
    RegistrationRecoveryReviewStatus,
)
from app.services.patient_registration_recovery_service import (
    RegistrationRecoveryInspection,
)


SUBJECT = "verified-supabase-subject"
PATIENT_ID = uuid.UUID("123e4567-e89b-12d3-a456-426614174001")
IDENTITY_ID = uuid.UUID("123e4567-e89b-12d3-a456-426614174002")
GRAPH = "a" * 64


def _inspection(reason: str = "IDENTITY_REVOKED") -> RegistrationRecoveryInspection:
    return RegistrationRecoveryInspection(
        disposition="manual_review",
        provider_subject=SUBJECT,
        patient_id=str(PATIENT_ID),
        target_patient_id=str(PATIENT_ID),
        graph_fingerprint=GRAPH,
        reason_code=reason,
    )


def _result(row):
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    return result


def _identity():
    return SimpleNamespace(
        identity_id=IDENTITY_ID,
        patient_id=PATIENT_ID,
        provider="supabase",
        provider_subject=SUBJECT,
    )


def _db(*execute_rows, patient_exists: bool = True):
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_result(row) for row in execute_rows])
    db.get = AsyncMock(return_value=SimpleNamespace() if patient_exists else None)
    db.flush = AsyncMock()
    db.add = MagicMock()
    return db


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("ERASURE_STATE_PRESENT", RegistrationRecoveryReviewReason.ERASURE_STATE_PRESENT),
        (
            "CANONICAL_ERASURE_STATE_PRESENT",
            RegistrationRecoveryReviewReason.ERASURE_STATE_PRESENT,
        ),
        ("IDENTITY_REVOKED", RegistrationRecoveryReviewReason.IDENTITY_REVOKED),
        (
            "MULTIPLE_SOURCE_IDENTITIES",
            RegistrationRecoveryReviewReason.MULTIPLE_IDENTITIES,
        ),
        (
            "CANONICAL_IDENTITY_CONFLICT",
            RegistrationRecoveryReviewReason.MULTIPLE_IDENTITIES,
        ),
        (
            "DELETED_PATIENT_WITHOUT_MERGE_TOMBSTONE",
            RegistrationRecoveryReviewReason.PATIENT_DELETED_WITHOUT_MERGE,
        ),
        ("MERGE_TOMBSTONE_CYCLE", RegistrationRecoveryReviewReason.MERGE_AMBIGUOUS),
        (
            "MERGE_TOMBSTONE_CHAIN_TOO_DEEP",
            RegistrationRecoveryReviewReason.MERGE_AMBIGUOUS,
        ),
        (
            "CANONICAL_PATIENT_UNAVAILABLE",
            RegistrationRecoveryReviewReason.MERGE_AMBIGUOUS,
        ),
        ("LINKED_PATIENT_MISSING", RegistrationRecoveryReviewReason.SECURITY_CONCERN),
        ("FUTURE_UNKNOWN_REASON", RegistrationRecoveryReviewReason.SECURITY_CONCERN),
        (None, RegistrationRecoveryReviewReason.SECURITY_CONCERN),
    ],
)
def test_manual_review_reason_normalization_is_closed(source, expected) -> None:
    assert review_service.normalize_registration_recovery_review_reason(source) == expected


def test_provider_subject_hash_is_stable_domain_separated_and_not_raw() -> None:
    digest = review_service.provider_subject_hash(SUBJECT)
    assert digest == review_service.provider_subject_hash(SUBJECT)
    assert len(digest) == 64
    assert digest != SUBJECT
    assert SUBJECT not in digest
    assert digest != review_service.provider_subject_hash("other-subject")


@pytest.mark.asyncio
async def test_case_creation_anchors_exact_identity_and_never_persists_raw_subject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _db(_identity(), None, None)
    audit = AsyncMock()
    monkeypatch.setattr(review_service, "enqueue_audit_event", audit)
    monkeypatch.setattr(review_service, "_case_reference", lambda: "RRC-TESTCASE0000000000000001")

    case = await review_service.open_registration_recovery_review_case(
        db,
        inspection=_inspection(),
        attempt_id="verified-attempt",
    )

    assert case.identity_id == IDENTITY_ID
    assert case.patient_id == PATIENT_ID
    assert case.provider == "supabase"
    assert case.provider_subject_hash == review_service.provider_subject_hash(SUBJECT)
    assert not hasattr(case, "provider_subject")
    assert case.graph_fingerprint == GRAPH
    assert case.reason_codes == [RegistrationRecoveryReviewReason.IDENTITY_REVOKED.value]
    assert case.status == RegistrationRecoveryReviewStatus.PENDING.value
    assert case.version == 1
    assert case.contract_version == REGISTRATION_RECOVERY_REVIEW_CONTRACT_VERSION
    assert case.policy_version == REGISTRATION_RECOVERY_REVIEW_POLICY_VERSION
    assert case.assigned_reviewer_id is None
    assert case.review_session_binding is None
    db.add.assert_called_once_with(case)
    db.flush.assert_awaited_once()

    audit.assert_awaited_once()
    audit_kwargs = audit.await_args.kwargs
    assert audit_kwargs["event_type"] == "PATIENT_REGISTRATION_RECOVERY_REVIEW_OPENED"
    assert audit_kwargs["target_id"] == case.case_reference
    assert SUBJECT not in str(audit_kwargs)
    assert GRAPH not in str(audit_kwargs)


@pytest.mark.asyncio
async def test_missing_patient_keeps_identity_anchor_but_not_stale_patient_fk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _db(_identity(), None, None, patient_exists=False)
    monkeypatch.setattr(review_service, "enqueue_audit_event", AsyncMock())

    case = await review_service.open_registration_recovery_review_case(
        db,
        inspection=_inspection("LINKED_PATIENT_MISSING"),
        attempt_id="verified-attempt",
    )

    assert case.identity_id == IDENTITY_ID
    assert case.patient_id is None
    assert case.reason_codes == [RegistrationRecoveryReviewReason.SECURITY_CONCERN.value]


@pytest.mark.asyncio
async def test_exact_existing_graph_case_is_idempotently_returned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inspection = _inspection("IDENTITY_REVOKED")
    subject_hash = review_service.provider_subject_hash(SUBJECT)
    operation_hash = review_service._operation_hash(
        {
            "contract_version": REGISTRATION_RECOVERY_REVIEW_CONTRACT_VERSION,
            "graph_fingerprint": GRAPH,
            "identity_id": str(IDENTITY_ID),
            "patient_id": str(PATIENT_ID),
            "policy_version": REGISTRATION_RECOVERY_REVIEW_POLICY_VERSION,
            "provider": "supabase",
            "provider_subject_hash": subject_hash,
            "reason_codes": [RegistrationRecoveryReviewReason.IDENTITY_REVOKED.value],
        }
    )
    existing = PatientRegistrationRecoveryReviewCase(
        id=uuid.uuid4(),
        case_reference="RRC-EXISTING00000000000001",
        provider="supabase",
        provider_subject_hash=subject_hash,
        identity_id=IDENTITY_ID,
        patient_id=PATIENT_ID,
        graph_fingerprint=GRAPH,
        reason_codes=[RegistrationRecoveryReviewReason.IDENTITY_REVOKED.value],
        status=RegistrationRecoveryReviewStatus.PENDING.value,
        version=1,
        assigned_reviewer_id=None,
        assigned_reviewer_role=None,
        reviewer_authority_version=None,
        review_session_binding=None,
        creation_idempotency_key="prior-attempt-key",
        creation_operation_hash=operation_hash,
        contract_version=REGISTRATION_RECOVERY_REVIEW_CONTRACT_VERSION,
        policy_version=REGISTRATION_RECOVERY_REVIEW_POLICY_VERSION,
        created_at=SimpleNamespace(),
        claimed_at=None,
        resolved_at=None,
    )
    db = _db(_identity(), None, existing)
    audit = AsyncMock()
    monkeypatch.setattr(review_service, "enqueue_audit_event", audit)

    result = await review_service.open_registration_recovery_review_case(
        db,
        inspection=inspection,
        attempt_id="new-verified-attempt",
    )

    assert result is existing
    db.add.assert_not_called()
    db.flush.assert_not_awaited()
    audit.assert_awaited_once()


@pytest.mark.asyncio
async def test_existing_graph_case_with_mismatched_identity_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject_hash = review_service.provider_subject_hash(SUBJECT)
    conflicting = SimpleNamespace(
        provider="supabase",
        provider_subject_hash=subject_hash,
        identity_id=uuid.uuid4(),
        patient_id=PATIENT_ID,
        graph_fingerprint=GRAPH,
        reason_codes=[RegistrationRecoveryReviewReason.IDENTITY_REVOKED.value],
        creation_operation_hash="b" * 64,
        contract_version=REGISTRATION_RECOVERY_REVIEW_CONTRACT_VERSION,
        policy_version=REGISTRATION_RECOVERY_REVIEW_POLICY_VERSION,
    )
    db = _db(_identity(), None, conflicting)
    audit = AsyncMock()
    monkeypatch.setattr(review_service, "enqueue_audit_event", audit)

    with pytest.raises(review_service.PatientRegistrationRecoveryReviewError) as exc_info:
        await review_service.open_registration_recovery_review_case(
            db,
            inspection=_inspection(),
            attempt_id="verified-attempt",
        )

    assert exc_info.value.code == review_service.REGISTRATION_RECOVERY_REVIEW_CASE_CONFLICT
    db.add.assert_not_called()
    audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_identity_binding_change_after_classification_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    moved_identity = _identity()
    moved_identity.patient_id = uuid.uuid4()
    db = _db(moved_identity)
    audit = AsyncMock()
    monkeypatch.setattr(review_service, "enqueue_audit_event", audit)

    with pytest.raises(review_service.PatientRegistrationRecoveryReviewError) as exc_info:
        await review_service.open_registration_recovery_review_case(
            db,
            inspection=_inspection(),
            attempt_id="verified-attempt",
        )

    assert exc_info.value.code == review_service.REGISTRATION_RECOVERY_REVIEW_CASE_CONFLICT
    db.get.assert_not_awaited()
    db.add.assert_not_called()
    audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_manual_or_malformed_graph_origin_is_rejected_before_database_use() -> None:
    db = MagicMock()
    db.execute = AsyncMock()
    invalid = RegistrationRecoveryInspection(
        disposition="repairable",
        provider_subject=SUBJECT,
        patient_id=str(PATIENT_ID),
        target_patient_id=str(PATIENT_ID),
        graph_fingerprint="not-a-sha256",
        repair_kind="restore_patient_record_anchor",
    )

    with pytest.raises(review_service.PatientRegistrationRecoveryReviewError) as exc_info:
        await review_service.open_registration_recovery_review_case(
            db,
            inspection=invalid,
            attempt_id="verified-attempt",
        )

    assert (
        exc_info.value.code
        == review_service.REGISTRATION_RECOVERY_REVIEW_CASE_INVALID_ORIGIN
    )
    db.execute.assert_not_awaited()
