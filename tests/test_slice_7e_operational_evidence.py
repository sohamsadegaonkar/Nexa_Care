from __future__ import annotations

from pathlib import Path

from scripts.verify_audit_integrity_evidence import classify_failure

ROOT = Path(__file__).resolve().parents[1]


def test_legacy_audit_verifier_is_only_partitioned_compatibility_wrapper() -> None:
    source = (ROOT / "scripts" / "verify_audit_chain.py").read_text(encoding="utf-8")

    assert "scripts.verify_audit_partitions" in source
    assert "partitioned_main" in source
    assert "FROM public.audit_ledger" not in source
    assert "expected_previous = \"GENESIS\"" not in source


def test_integration_suite_uses_canonical_partition_verifier() -> None:
    source = (ROOT / "scripts" / "run_integration_suite.sh").read_text(
        encoding="utf-8"
    )

    assert "python -m scripts.verify_audit_partitions --dry-run" in source
    assert "python scripts/verify_audit_chain.py" not in source


def test_sanitized_audit_failure_classification_never_requires_raw_values() -> None:
    samples = {
        "duplicate record_hash abc": "DUPLICATE_RECORD_HASH",
        "expected exactly 1 genesis event, found 2": "GENESIS_CARDINALITY",
        "multiple successors for hash abc (fork/cycle)": "CHAIN_FORK_OR_CYCLE",
        "record_hash mismatch at audit_id=123": "RECORD_HASH_MISMATCH",
        "sequence discontinuity at audit_id=123": "SEQUENCE_DISCONTINUITY",
        "head_hash mismatch: stored=x calculated=y": "HEAD_HASH_MISMATCH",
        "some future integrity failure containing sensitive internals": (
            "UNCLASSIFIED_INTEGRITY_FAILURE"
        ),
    }

    assert {classify_failure(reason): expected for reason, expected in samples.items()} == {
        expected: expected for expected in samples.values()
    }


def test_retention_decision_remains_human_gated_and_not_in_effect() -> None:
    decision = (
        ROOT / "docs" / "governance" / "MILESTONE_6_PILOT_RETENTION_DECISION.md"
    ).read_text(encoding="utf-8-sig")

    assert "# DRAFT — NOT APPROVED — NOT IN EFFECT" in decision
    assert "Status: **PENDING APPROVAL**" in decision
    assert "Security reviewer\n\nName: **UNASSIGNED**" in decision
    assert "Privacy / legal reviewer\n\nName: **UNASSIGNED**" in decision
    assert "**DO NOT CONFIGURE THE S3 LIFECYCLE RULE.**" in decision


def test_pending_retention_has_no_s3_lifecycle_implementation() -> None:
    decision = (
        ROOT / "docs" / "governance" / "MILESTONE_6_PILOT_RETENTION_DECISION.md"
    ).read_text(encoding="utf-8-sig")
    assert "Status: **PENDING APPROVAL**" in decision

    forbidden = (
        "put_bucket_lifecycle",
        "put_bucket_lifecycle_configuration",
        "LifecycleConfiguration",
        "NoncurrentVersionExpiration",
        "AbortIncompleteMultipartUpload",
    )
    roots = [ROOT / "app", ROOT / "scripts", ROOT / ".github", ROOT / "infra"]
    offenders: list[str] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in {
                ".py",
                ".json",
                ".yaml",
                ".yml",
                ".tf",
            }:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if any(token in text for token in forbidden):
                offenders.append(str(path.relative_to(ROOT)))

    assert offenders == []
