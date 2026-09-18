from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import uuid
from unittest.mock import AsyncMock, MagicMock

from fastapi import HTTPException
import pytest

import app.services.patient_external_record_review as review_module
from app.services.crypto_kms import EncryptedField
from app.services.patient_external_record_review import (
    _candidate_contexts,
    _display_label,
    _load_owned_import_for_review,
    _normalized_correction,
    _public_review_status,
    get_patient_external_record_review,
    review_patient_external_record_candidate,
)


def _serialized(field_name: str, value: bytes = b"value") -> str:
    return EncryptedField(
        ciphertext=value,
        iv=b"0" * 12,
        field_name=field_name,
        dek_version=1,
        algorithm="AES-256-GCM",
    ).serialize()


def test_review_service_accepts_only_patient_import_authority() -> None:
    assert set(get_patient_external_record_review.__annotations__) >= {
        "patient_id",
        "import_id",
    }
    parameter_names = set(review_patient_external_record_candidate.__annotations__)
    assert {"patient_id", "import_id", "candidate_id", "decision"} <= parameter_names
    assert {
        "provider_id",
        "hospital_id",
        "tenant_id",
        "consent_token",
        "clinical_access_session_id",
    }.isdisjoint(parameter_names)


def test_review_status_and_label_projection_are_patient_friendly() -> None:
    assert _public_review_status("NEEDS_REVIEW") == "pending"
    assert _public_review_status("ACCEPTED") == "accepted"
    assert _public_review_status("CORRECTED") == "corrected"
    assert _public_review_status("REJECTED") == "rejected"
    assert _display_label("hba1c_result") == "Hba1C Result"


def test_review_contexts_separate_extracted_source_and_patient_correction() -> None:
    candidate_id = uuid.uuid4()
    raw, source, reviewed = _candidate_contexts(candidate_id)
    assert raw.endswith(str(candidate_id))
    assert source.endswith(str(candidate_id))
    assert reviewed.endswith(str(candidate_id))
    assert len({raw, source, reviewed}) == 3


def test_correction_validation_is_explicit_and_bounded() -> None:
    assert _normalized_correction("correct", "  6.1%  ") == "6.1%"
    assert _normalized_correction("accept", None) is None
    assert _normalized_correction("reject", None) is None

    for decision, value, code in [
        ("unknown", None, "INVALID_REVIEW_DECISION"),
        ("accept", "unexpected", "UNEXPECTED_CORRECTION_VALUE"),
        ("correct", None, "CORRECTION_VALUE_REQUIRED"),
        ("correct", "   ", "CORRECTION_VALUE_REQUIRED"),
        ("correct", "x" * 4097, "CORRECTION_VALUE_TOO_LONG"),
    ]:
        with pytest.raises(HTTPException) as exc_info:
            _normalized_correction(decision, value)
        assert exc_info.value.status_code == 422
        assert exc_info.value.detail["error_code"] == code


@pytest.mark.asyncio
async def test_owned_import_review_lookup_binds_patient_and_can_lock() -> None:
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute.return_value = result
    patient_id = uuid.uuid4()
    import_id = uuid.uuid4()

    await _load_owned_import_for_review(
        db,
        patient_id=patient_id,
        import_id=import_id,
        for_update=True,
    )

    statement = db.execute.await_args.args[0]
    values = set(statement.compile().params.values())
    assert patient_id in values
    assert import_id in values
    assert "patient_external_record_imports.patient_id" in str(statement)
    assert "FOR UPDATE" in str(statement).upper()


@pytest.mark.asyncio
async def test_review_snapshot_preserves_extracted_and_corrected_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = AsyncMock()
    patient_id = uuid.uuid4()
    import_id = uuid.uuid4()
    candidate_id = uuid.uuid4()
    row = SimpleNamespace(
        id=import_id,
        patient_id=patient_id,
        source_document_id=uuid.uuid4(),
        category="LAB_REPORT",
        status="READY_TO_SAVE",
    )
    raw_context, source_context, reviewed_context = _candidate_contexts(candidate_id)
    candidate = SimpleNamespace(
        id=candidate_id,
        import_id=import_id,
        patient_id=patient_id,
        source_document_id=row.source_document_id,
        field_name="hba1c",
        encrypted_raw_value=_serialized(raw_context),
        encrypted_source_text=_serialized(source_context),
        encrypted_reviewed_value=_serialized(reviewed_context),
        source_page=1,
        evidence_complete=True,
        review_status="CORRECTED",
    )

    async def load_import(*args, **kwargs):
        return row

    async def active(*args, **kwargs):
        return None

    async def load_candidates(*args, **kwargs):
        return [candidate]

    monkeypatch.setattr(review_module, "_load_owned_import_for_review", load_import)
    monkeypatch.setattr(review_module, "_assert_patient_active", active)
    monkeypatch.setattr(review_module, "_load_owned_candidates", load_candidates)

    kms = MagicMock()
    kms.decrypt_field = AsyncMock(
        side_effect=["6.4%", "HbA1c 6.4%", "6.1%"]
    )
    monkeypatch.setattr(review_module, "get_encryption_provider", lambda: kms)

    snapshot = await get_patient_external_record_review(
        db,
        patient_id=str(patient_id),
        import_id=import_id,
    )

    assert snapshot.status == "ready_to_save"
    assert len(snapshot.items) == 1
    item = snapshot.items[0]
    assert item.extracted_value == "6.4%"
    assert item.corrected_value == "6.1%"
    assert item.source_text == "HbA1c 6.4%"
    assert item.decision == "corrected"
    assert item.confirmation_required is True


@pytest.mark.asyncio
async def test_correcting_last_candidate_advances_ready_to_save(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = AsyncMock()
    patient_id = uuid.uuid4()
    import_id = uuid.uuid4()
    candidate_id = uuid.uuid4()
    row = SimpleNamespace(
        id=import_id,
        source_document_id=uuid.uuid4(),
        status="REVIEW_REQUIRED",
        review_completed_at=None,
    )
    candidate = SimpleNamespace(
        id=candidate_id,
        import_id=import_id,
        patient_id=patient_id,
        source_document_id=row.source_document_id,
        encrypted_reviewed_value=None,
        review_status="NEEDS_REVIEW",
        patient_reviewed=False,
        reviewed_at=None,
    )

    async def load_import(*args, **kwargs):
        return row

    async def active(*args, **kwargs):
        return None

    async def load_candidate(*args, **kwargs):
        return candidate

    monkeypatch.setattr(review_module, "_load_owned_import_for_review", load_import)
    monkeypatch.setattr(review_module, "_assert_patient_active", active)
    monkeypatch.setattr(
        review_module,
        "_load_owned_candidate_for_update",
        load_candidate,
    )

    encrypted = MagicMock()
    encrypted.serialize.return_value = "ciphertext:1"
    kms = MagicMock()
    kms.ensure_active_dek = AsyncMock()
    kms.encrypt_field = AsyncMock(return_value=encrypted)
    monkeypatch.setattr(review_module, "get_encryption_provider", lambda: kms)

    count_result = MagicMock()
    count_result.scalar_one.return_value = 0
    db.execute.return_value = count_result
    audit = AsyncMock()
    monkeypatch.setattr(review_module, "enqueue_audit_event", audit)
    monkeypatch.setattr(
        review_module,
        "current_audit_context",
        lambda domain: SimpleNamespace(),
    )

    returned = await review_patient_external_record_candidate(
        db,
        patient_id=str(patient_id),
        import_id=import_id,
        candidate_id=candidate_id,
        decision="correct",
        corrected_value="  corrected value  ",
    )

    assert returned is row
    assert candidate.review_status == "CORRECTED"
    assert candidate.patient_reviewed is True
    assert candidate.encrypted_reviewed_value == "ciphertext:1"
    assert candidate.reviewed_at is not None
    assert row.status == "READY_TO_SAVE"
    assert row.review_completed_at is not None
    kms.encrypt_field.assert_awaited_once()
    db.commit.assert_awaited_once()
    assert audit.await_args.kwargs["metadata"] == {
        "authority": "patient_self",
        "decision": "correct",
        "review_complete": True,
    }


@pytest.mark.asyncio
async def test_accept_does_not_copy_plaintext_into_reviewed_ciphertext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = AsyncMock()
    patient_id = uuid.uuid4()
    import_id = uuid.uuid4()
    row = SimpleNamespace(
        id=import_id,
        source_document_id=uuid.uuid4(),
        status="REVIEW_REQUIRED",
        review_completed_at=None,
    )
    candidate = SimpleNamespace(
        id=uuid.uuid4(),
        encrypted_reviewed_value="old-correction:1",
        review_status="CORRECTED",
        patient_reviewed=True,
        reviewed_at=datetime.now(timezone.utc),
    )

    async def load_import(*args, **kwargs):
        return row

    async def active(*args, **kwargs):
        return None

    async def load_candidate(*args, **kwargs):
        return candidate

    monkeypatch.setattr(review_module, "_load_owned_import_for_review", load_import)
    monkeypatch.setattr(review_module, "_assert_patient_active", active)
    monkeypatch.setattr(
        review_module,
        "_load_owned_candidate_for_update",
        load_candidate,
    )
    count_result = MagicMock()
    count_result.scalar_one.return_value = 0
    db.execute.return_value = count_result
    monkeypatch.setattr(review_module, "enqueue_audit_event", AsyncMock())
    monkeypatch.setattr(
        review_module,
        "current_audit_context",
        lambda domain: SimpleNamespace(),
    )

    await review_patient_external_record_candidate(
        db,
        patient_id=str(patient_id),
        import_id=import_id,
        candidate_id=candidate.id,
        decision="accept",
    )

    assert candidate.review_status == "ACCEPTED"
    assert candidate.encrypted_reviewed_value is None
    assert candidate.patient_reviewed is True
    assert row.status == "READY_TO_SAVE"
