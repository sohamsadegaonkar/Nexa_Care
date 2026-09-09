#!/usr/bin/env python3
"""Compatibility entry point for Nexa Care audit-chain verification.

The canonical audit ledger is partitioned by ``chain_scope`` and has durable
``audit_chain_heads`` metadata. The historical single-GENESIS verifier that used
to live at this path is no longer correct for that model: multiple healthy
partitions would look like forks/orphans to a global-chain walk.

Operational verification is implemented by ``scripts.verify_audit_partitions``.
This wrapper remains only so older runbooks/operators fail safely into the
current verifier instead of executing stale integrity logic.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.verify_audit_partitions import main as partitioned_main  # noqa: E402


def main() -> int:
    return partitioned_main()


if __name__ == "__main__":
    raise SystemExit(main())
