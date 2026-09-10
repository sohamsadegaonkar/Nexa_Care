"""Focused contracts for Slice 9A manual-review case creation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import app.services.patient_registration_recovery_review_service as review_service
import app.services.patient_registration_recovery_service as recovery_service
from app.models.patient_registration_recovery_review import RegistrationRecoveryReviewReason
from app.services.patient_registration_recovery_service import RegistrationRecoveryInspection


def _inspection(
    *,
    disposition: str = "manual_review",
    reason_code: str | None = "IDENTITY_REVOKED",
    graph_fingerprint: str = "a" * 64,
) -> RegistrationRecoveryInspection:
    return RegistrationRecoveryInspection(
        disposition=disposition,
        provider_subject="provider-subject-123",
        patient_id="11111111-1111-1111-1111-111111111111",
        target_patient_id="11111111-1111-1111-1111-111111111111",
        graph_fingerprint=graph_fingerprint,
        repair_kind=None,
        reason_code=reason_code,
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("IDENTITY_REVOKED", RegistrationRecoveryReviewReason.IDENTITY_REVOKED.value),
        ("ERASURE_STATE_PRESENT", RegistrationRecoveryReviewReason.ERASURE_STATE_PRESENT.value),
        (
            "CANONICAL_ERASURE_STATE_PRESENT",
            RegistrationRecoveryReviewReason.ERASURE_STATE_PRESENT.value,
        ),
        (
            "MULTIPLE_SOURCE_IDENTITIES",
            RegistrationRecoveryReviewReason.MULTIPLE_IDENTITIES.value,
        ),
        (
            "CANONICAL_IDENTITY_CONFLICT",
            RegistrationRecoveryReviewReason.MULTIPLE_IDENTITIES.value,
        ),
        ("MERGE_TOMBSTONE_CYCLE", RegistrationRecoveryReviewReason.MERGE_AMBIGUOUS.value),
        (
            "MERGE_TOMBSTONE_CHAIN_TOO_DEEP",
            RegistrationRecoveryReviewReason.MERGE_AMBIGUOUS.value,
        ),
        (
            "DELETED_PATIENT_WITHOUT_MERGE_TOMBSTONE",
            RegistrationRecoveryReviewReason.PATIENT_DELETED_WITHOUT_MERGE.value,
        ),
        ("LINKED_PATIENT_MISSING", RegistrationRecoveryReviewReason.SECURITY_CONCERN.value),
        ("UNRECOGNIZED_FUTURE_REASON", RegistrationRecoveryReviewReason.SECURITY_CONCERN.value),
        (None, RegistrationRecoveryReviewReason.SECURITY_CONCERN.value),
    ],
)
def test_classifier_reasons_are_normalized_to_closed_review_vocabulary(
    raw: str | None,
    expected: str,
) -> None:
    assert review_service._normalize_reasons(raw) == [expected]


def test_case_creation_idempotency_is_graph_bound_not_attempt_bound() -> None:
    subject_hash = review_service._provider_subject_hash("provider-subject-123")
    first = review_service._creation_idempotency_key(
        subject_hash=subject_hash,
        graph_fingerprint="a" * 64,
    )
    second = review_service._creation_idempotency_key(
        subject_hash=subject_hash,
        graph_fingerprint="a" * 64,
    )
    changed_graph = review_service._creation_idempotency_key(
        subject_hash=subject_hash,
        graph_fingerprint="b" * 64,
    )

    assert first == second
    assert first != changed_graph
    assert "provider-subject-123" not in first


@pytest.mark.asyncio
async def test_case_creation_rejects_non_manual_classification_before_db_use() -> None:
    db = MagicMock()
    with pytest.raises(review_service.RegistrationRecoveryReviewCaseError) as exc_info:
        await review_service.create_registration_recovery_review_case(
            db,
            inspection=_inspection(disposition="repairable"),
        )

    assert exc_info.value.code == "REGISTRATION_RECOVERY_REVIEW_NOT_REQUIRED"
    db.scalar.assert_not_called()


@pytest.mark.asyncio
async def test_required_audit_bridge_opens_case_only_for_verified_manual_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = MagicMock()
    create_case = AsyncMock()
    enqueue = AsyncMock()
    monkeypatch.setattr(review_service, "create_registration_recovery_review_case", create_case)
    monkeypatch.setattr(recovery_service, "enqueue_audit_event", enqueue)
    monkeypatch.setattr(
        recovery_service,
        "current_audit_context",
        lambda _domain: MagicMock(),
    )
    inspection = _inspection()

    await recovery_service.audit_registration_recovery_required(
        db,
        inspection=inspection,
        attempt_id="verified-attempt",
    )

    create_case.assert_awaited_once_with(db, inspection=inspection)
    enqueue.assert_awaited_once()
    kwargs = enqueue.await_args.kwargs
    assert kwargs["event_type"] == "PATIENT_REGISTRATION_RECOVERY_REQUIRED"
    assert kwargs["status"] == "MANUAL_REVIEW"


@pytest.mark.asyncio
async def test_required_audit_bridge_does_not_open_case_for_automatic_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = MagicMock()
    create_case = AsyncMock()
    enqueue = AsyncMock()
    monkeypatch.setattr(review_service, "create_registration_recovery_review_case", create_case)
    monkeypatch.setattr(recovery_service, "enqueue_audit_event", enqueue)
    monkeypatch.setattr(
        recovery_service,
        "current_audit_context",
        lambda _domain: MagicMock(),
    )
    inspection = _inspection(
        disposition="repairable",
        reason_code="PATIENT_RECORD_ANCHOR_MISSING",
    )

    await recovery_service.audit_registration_recovery_required(
        db,
        inspection=inspection,
        attempt_id="verified-attempt",
    )

    create_case.assert_not_awaited()
    enqueue.assert_awaited_once()
    assert enqueue.await_args.kwargs["status"] == "REQUIRED"
