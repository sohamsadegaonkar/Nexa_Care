from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "textract-live-accuracy.yml"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_textract_live_gate_is_manual_protected_and_oidc_only() -> None:
    text = _workflow()
    assert "workflow_dispatch:" in text
    assert "environment: pilot" in text
    assert "id-token: write" in text
    assert "NEXA_PILOT_AWS_ROLE_ARN" in text
    assert "aws-actions/configure-aws-credentials@v4" in text
    assert "BLOCKED_MISSING_OIDC_ROLE" in text
    for static_secret in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        assert static_secret not in text


def test_textract_live_gate_requires_full_synthetic_corpus_provider_execution() -> None:
    text = _workflow()
    assert "tests/ai_extraction/benchmark/documents" in text
    assert "tests/ai_extraction/benchmark/synthetic-manifest.json" in text
    assert "provider_mode') != 'live_capture'" in text
    assert "attempted_documents') != 15" in text
    assert "live_provider_calls') != 15" in text
    assert "TEXTRACT_LIVE_QUALIFICATION=PASS" in text


def test_textract_live_gate_requires_sanitized_replay_reproduction() -> None:
    text = _workflow()
    assert "--capture-sanitized-replay" in text
    assert "--replay-sanitized" in text
    assert "identity_case_decisions" in text
    assert "replay.get('live_provider_calls') != 0" in text
    assert "SANITIZED_REPLAY_REPRODUCTION=PASS" in text


def test_textract_live_gate_does_not_weaken_identity_policy() -> None:
    text = _workflow()
    forbidden = ("fuzzy", "levenshtein", "rapidfuzz", "difflib", "identity threshold")
    lowered = text.lower()
    for term in forbidden:
        assert term not in lowered
