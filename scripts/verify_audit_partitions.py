"""Full cryptographic verifier for the partitioned audit chain.

This is the operator-run equivalent of what the old runtime append used to
do on every single write (full-chain read + validation) -- now done
out-of-band, on demand, once per partition, instead of on every append.

For each chain_partition:
  - walk from GENESIS, recalculating every record_hash from its stored
    payload and previous_hash
  - confirm exact sequence-number continuity (1..N, no gaps, no dupes)
  - confirm every event in the partition was visited (no disconnected
    events)
  - compare the calculated tip against audit_chain_heads: head_hash,
    head_event_id, sequence_number

On any mismatch, marks the partition unhealthy (audit_chain_heads.healthy
= FALSE) -- which makes the runtime append path fail closed for that
partition until an operator resolves it -- and emits a safe security
alert (no raw exception text, no PII).

Usage:
    python -m scripts.verify_audit_partitions [--partition NAME] [--dry-run]

Exit code is 0 if every checked partition is healthy, 1 otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from app.core.database import get_async_engine  # noqa: E402

logger = logging.getLogger("nexa_security")

_SELECT_PARTITIONS_SQL = text("SELECT DISTINCT chain_scope FROM public.audit_ledger")
_SELECT_PARTITION_EVENTS_SQL = text(
    """
    SELECT audit_id, previous_hash, record_hash, details, sequence_number
    FROM public.audit_ledger
    WHERE chain_scope = :chain_partition
    """
)
_SELECT_HEAD_SQL = text(
    "SELECT head_event_id, head_hash, sequence_number, healthy FROM public.audit_chain_heads WHERE chain_partition = :chain_partition"
)
_MARK_UNHEALTHY_SQL = text(
    "UPDATE public.audit_chain_heads SET healthy = FALSE, updated_at = now() WHERE chain_partition = :chain_partition"
)


class VerificationFailure:
    def __init__(self, chain_partition: str, reason: str):
        self.chain_partition = chain_partition
        self.reason = reason

    def __str__(self) -> str:
        return f"{self.chain_partition}: {self.reason}"


def _calculate_hash(payload: dict, previous_hash: str) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256((canonical + previous_hash).encode("utf-8")).hexdigest()


async def verify_partition(connection, chain_partition: str, *, dry_run: bool) -> VerificationFailure | None:
    rows = list(
        (await connection.execute(_SELECT_PARTITION_EVENTS_SQL, {"chain_partition": chain_partition})).mappings()
    )
    head_row = (
        (await connection.execute(_SELECT_HEAD_SQL, {"chain_partition": chain_partition})).mappings().first()
    )

    async def fail(reason: str) -> VerificationFailure:
        logger.critical(json.dumps({
            "event": "audit_partition_verification_failed", "severity": "critical",
            "chain_partition": chain_partition, "reason": reason,
        }))
        if not dry_run:
            await connection.execute(_MARK_UNHEALTHY_SQL, {"chain_partition": chain_partition})
        return VerificationFailure(chain_partition, reason)

    if not rows:
        if head_row is not None:
            return await fail("head row exists but partition has zero events")
        return None  # Empty partition with no head -- valid.

    by_hash = {}
    predecessor_of = {}
    for row in rows:
        record_hash = row["record_hash"]
        if record_hash in by_hash:
            return await fail(f"duplicate record_hash {record_hash!r}")
        by_hash[record_hash] = row
        predecessor_of[record_hash] = row["previous_hash"]

    genesis = [h for h, prev in predecessor_of.items() if prev == "GENESIS"]
    if len(genesis) != 1:
        return await fail(f"expected exactly 1 genesis event, found {len(genesis)}")

    successors: dict[str, list[str]] = {}
    for record_hash, previous_hash in predecessor_of.items():
        successors.setdefault(previous_hash, []).append(record_hash)
    for previous_hash, children in successors.items():
        if len(children) > 1:
            return await fail(f"multiple successors for hash {previous_hash!r} (fork/cycle): {children}")

    ordered = []
    current = genesis[0]
    seen: set[str] = set()
    while True:
        if current in seen:
            return await fail(f"cycle detected at hash {current!r}")
        seen.add(current)
        row = by_hash[current]
        details = row["details"]
        if isinstance(details, str):
            details = json.loads(details)
        recalculated = _calculate_hash(details, row["previous_hash"])
        if recalculated != current:
            return await fail(f"record_hash mismatch at audit_id={row['audit_id']}")
        ordered.append(row)
        nxt = successors.get(current, [])
        if not nxt:
            break
        current = nxt[0]

    if len(ordered) != len(rows):
        return await fail(f"disconnected component: reached {len(ordered)} of {len(rows)} events")

    for expected_seq, row in enumerate(ordered, start=1):
        if row["sequence_number"] != expected_seq:
            return await fail(
                f"sequence discontinuity at audit_id={row['audit_id']}: "
                f"expected {expected_seq}, got {row['sequence_number']}"
            )

    tip = ordered[-1]
    if head_row is None:
        return await fail("no chain_chain_heads row exists for a non-empty partition")
    if head_row["head_hash"] != tip["record_hash"]:
        return await fail(f"head_hash mismatch: stored={head_row['head_hash']} calculated={tip['record_hash']}")
    if head_row["head_event_id"] != tip["audit_id"]:
        return await fail(f"head_event_id mismatch: stored={head_row['head_event_id']} calculated={tip['audit_id']}")
    if head_row["sequence_number"] != len(ordered):
        return await fail(f"head sequence_number mismatch: stored={head_row['sequence_number']} calculated={len(ordered)}")

    return None


async def verify_all(partition: str | None = None, *, dry_run: bool = False) -> list[VerificationFailure]:
    engine = get_async_engine()
    async with engine.begin() as connection:
        if partition:
            partitions = [partition]
        else:
            partitions = [row[0] for row in (await connection.execute(_SELECT_PARTITIONS_SQL)).fetchall()]

        failures = []
        for chain_partition in partitions:
            result = await verify_partition(connection, chain_partition, dry_run=dry_run)
            if result is not None:
                failures.append(result)
        return failures


async def _main(partition: str | None, dry_run: bool) -> int:
    failures = await verify_all(partition, dry_run=dry_run)
    if not failures:
        print("Audit chain verification: OK, all partitions healthy.")
        return 0

    print(f"Audit chain verification: {len(failures)} partition(s) failed verification:")
    for failure in failures:
        print(f"  - {failure}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--partition", default=None, help="Verify only this chain_partition instead of all of them.")
    parser.add_argument("--dry-run", action="store_true", help="Report only; do not mark any partition unhealthy.")
    args = parser.parse_args()
    return asyncio.run(_main(args.partition, args.dry_run))


if __name__ == "__main__":
    sys.exit(main())