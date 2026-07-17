"""Human-review and commit regression guards."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE = (ROOT / "app/api/v2/pipeline_routes.py").read_text(encoding="utf-8")


def test_review_only_accepts_explicit_actions():
    for action in ("approve", "reject", "edit", "approved", "rejected", "edited"):
        assert f'"{action}":' in CODE


def test_review_rejects_stale_decision_and_version():
    assert "STALE_REVIEW_DECISION" in CODE
    assert "STALE_REVIEW_VERSION" in CODE


def test_edit_records_original_and_corrected_values():
    assert "FieldCorrection(" in CODE
    assert "original_value=field.raw_value" in CODE
    assert "corrected_value=payload.corrected_value" in CODE


def test_review_queue_item_is_adjudicated_by_authenticated_provider():
    assert 'qi.status = "adjudicated"' in CODE
    assert "qi.adjudicated_by = provider.actor_uid" in CODE


def test_commit_blocks_unresolved_fields_and_ignores_rejected_fields():
    assert 'ExtractedFieldRecord.status == "needs_review"' in CODE
    assert 'ExtractedFieldRecord.status.in_(["approved", "edited"])' in CODE


def test_commit_response_contains_no_fabricated_ledger_hash():
    assert '"ledger_tx_hash": None' in CODE
    assert "a8f902" not in CODE
