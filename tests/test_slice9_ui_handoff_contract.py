"""Qualification smoke for the Slice 9A UI handoff contract."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_slice9_ui_handoff_names_public_status_contract() -> None:
    contract = (ROOT / "docs" / "governance" / "SLICE_9A_UI_HANDOFF.md").read_text(
        encoding="utf-8"
    )
    assert "/api/v2/auth/registration-recovery/review/cases/{case_reference}" in contract
    assert "WAIT_FOR_REVIEW" in contract
    assert "RESTART_ACCOUNT_RECOVERY" in contract
    assert "CONTACT_SUPPORT" in contract
    assert "must not treat `case_reference` as a bearer repair credential" in contract
