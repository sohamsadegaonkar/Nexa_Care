#!/usr/bin/env python3
"""Emit sanitized machine-readable evidence for canonical audit integrity.

This wrapper always invokes the partition-aware verifier in dry-run mode. It
returns only failure classifications and counts, never stored audit payloads,
hashes, event IDs, or raw database exceptions.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from scripts.verify_audit_partitions import verify_all

EVIDENCE_SCHEMA = "nexa-slice-7e-audit-integrity-evidence-v1"

_REASON_CODES = (
    ("duplicate record_hash", "DUPLICATE_RECORD_HASH"),
    ("expected exactly 1 genesis", "GENESIS_CARDINALITY"),
    ("multiple successors", "CHAIN_FORK_OR_CYCLE"),
    ("cycle detected", "CHAIN_CYCLE"),
    ("invalid details payload", "INVALID_PAYLOAD"),
    ("protocol_version mismatch", "PROTOCOL_VERSION_MISMATCH"),
    ("chain_scope mismatch", "CHAIN_SCOPE_MISMATCH"),
    ("unsupported protocol_version", "UNSUPPORTED_PROTOCOL_VERSION"),
    ("record_hash mismatch", "RECORD_HASH_MISMATCH"),
    ("disconnected component", "DISCONNECTED_COMPONENT"),
    ("sequence discontinuity", "SEQUENCE_DISCONTINUITY"),
    ("head row exists but partition has zero events", "ORPHAN_HEAD"),
    ("no chain_chain_heads row", "MISSING_CHAIN_HEAD"),
    ("head_hash mismatch", "HEAD_HASH_MISMATCH"),
    ("head_event_id mismatch", "HEAD_EVENT_MISMATCH"),
    ("head sequence_number mismatch", "HEAD_SEQUENCE_MISMATCH"),
)


def classify_failure(reason: str) -> str:
    """Map a detailed operator-only reason to a value-free evidence code."""
    for fragment, code in _REASON_CODES:
        if fragment in reason:
            return code
    return "UNCLASSIFIED_INTEGRITY_FAILURE"


async def build_evidence(partition: str | None = None) -> dict[str, object]:
    failures = await verify_all(partition, dry_run=True)
    codes = sorted({classify_failure(failure.reason) for failure in failures})
    return {
        "schema": EVIDENCE_SCHEMA,
        "status": "PASS" if not failures else "FAIL",
        "dry_run": True,
        "scope": "single-partition" if partition else "all-partitions",
        "failure_count": len(failures),
        "failure_codes": codes,
        "raw_audit_payloads_included": False,
        "raw_hashes_or_event_ids_included": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--partition",
        default=None,
        help="optionally verify one chain partition; value is not emitted",
    )
    arguments = parser.parse_args()
    try:
        evidence = asyncio.run(build_evidence(arguments.partition))
    except Exception:
        print(
            json.dumps(
                {
                    "schema": EVIDENCE_SCHEMA,
                    "status": "ERROR",
                    "dry_run": True,
                    "failure_count": 0,
                    "failure_codes": ["VERIFIER_EXECUTION_ERROR"],
                    "raw_audit_payloads_included": False,
                    "raw_hashes_or_event_ids_included": False,
                },
                sort_keys=True,
            )
        )
        return 1

    print(json.dumps(evidence, sort_keys=True))
    return 0 if evidence["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
