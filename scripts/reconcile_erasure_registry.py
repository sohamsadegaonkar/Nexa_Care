"""Reconcile the erasure registry against real KMS state.

Run periodically (cron/scheduled task). For every tombstone:

  - status == deletion_scheduled and wrapping_key_type == patient:
      call KMS describe_key; if the key is gone / PendingDeletion has
      elapsed, advance the tombstone to destroyed. If describe_key itself
      fails, or the key is still active/enabled when it shouldn't be,
      that's an inconsistency.
  - status == operator_action_required: reported but not auto-resolved --
      these need a human, by design.
  - status == access_blocked and wrapping_key_type == shared: nothing to
      reconcile against KMS (the shared CMK is never touched), but the
      DEK rows must actually be gone.

Exits 0 if every tombstone is consistent, 1 otherwise -- an operator or a
monitoring job should treat a nonzero exit as an incident.

Usage:
    python -m scripts.reconcile_erasure_registry [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from app.core.database import get_session_factory  # noqa: E402
from app.models.dek_store import PatientDEKStore  # noqa: E402
from app.models.erasure_tombstone import ErasureStatus, PatientErasureTombstone  # noqa: E402
from app.security.erasure_registry import mark_destroyed, mark_operator_action_required  # noqa: E402
from app.services.crypto_kms import get_encryption_provider  # noqa: E402

logger = logging.getLogger("nexa_logger")


class Inconsistency:
    def __init__(self, patient_ref: str, reason: str):
        self.patient_ref = patient_ref
        self.reason = reason

    def __str__(self) -> str:
        return f"{self.patient_ref}: {self.reason}"


async def _reconcile_deletion_scheduled(tombstone: PatientErasureTombstone, kms, db, dry_run: bool) -> Inconsistency | None:
    if tombstone.wrapping_key_type != "patient" or not tombstone.patient_wrapping_key_id:
        return Inconsistency(tombstone.patient_ref, "deletion_scheduled but no patient_wrapping_key_id on record")

    describe = getattr(kms, "_kms", None)
    if describe is None:
        # Local provider: there is no external KMS to poll. Local
        # destroy_dek() already advances straight to destroyed, so a
        # deletion_scheduled local tombstone that never advanced is itself
        # the inconsistency.
        return Inconsistency(tombstone.patient_ref, "local provider tombstone stuck in deletion_scheduled")

    try:
        response = await asyncio.to_thread(describe.describe_key, KeyId=tombstone.patient_wrapping_key_id)
        key_state = response["KeyMetadata"].get("KeyState")
    except Exception as exc:  # noqa: BLE001
        return Inconsistency(tombstone.patient_ref, f"describe_key failed: {type(exc).__name__}: {exc}")

    if key_state in {"PendingDeletion", "Disabled"}:
        deletion_date = response["KeyMetadata"].get("DeletionDate")
        if deletion_date is not None and deletion_date.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc):
            if not dry_run:
                await mark_destroyed(db, tombstone)
                await db.commit()
            return None
        return None  # Still within the pending window -- not an inconsistency, just not done yet.

    if key_state == "Enabled":
        # The key should have been disabled by destroy_dek() and was not --
        # a real, actionable inconsistency.
        reason = f"key {tombstone.patient_wrapping_key_id} is still Enabled but tombstone says deletion_scheduled"
        if not dry_run:
            await mark_operator_action_required(db, tombstone, failure_code="key_not_disabled_on_reconcile")
            await db.commit()
        return Inconsistency(tombstone.patient_ref, reason)

    return None


async def _reconcile_access_blocked(tombstone: PatientErasureTombstone, db) -> Inconsistency | None:
    remaining = (
        await db.execute(select(PatientDEKStore.id).where(PatientDEKStore.patient_id == tombstone.patient_ref))
    ).scalars().all()
    if remaining:
        return Inconsistency(
            tombstone.patient_ref,
            f"status=access_blocked but {len(remaining)} DEK row(s) still present",
        )
    return None


async def reconcile(dry_run: bool = False) -> list[Inconsistency]:
    session_factory = get_session_factory()
    kms = get_encryption_provider()
    inconsistencies: list[Inconsistency] = []

    async with session_factory() as db:
        tombstones = (await db.execute(select(PatientErasureTombstone))).scalars().all()

        for tombstone in tombstones:
            if tombstone.status == ErasureStatus.OPERATOR_ACTION_REQUIRED.value:
                inconsistencies.append(
                    Inconsistency(tombstone.patient_ref, f"awaiting operator action ({tombstone.failure_code})")
                )
                continue

            if tombstone.status == ErasureStatus.DELETION_SCHEDULED.value:
                result = await _reconcile_deletion_scheduled(tombstone, kms, db, dry_run)
                if result:
                    inconsistencies.append(result)
                continue

            if tombstone.status in {ErasureStatus.ACCESS_BLOCKED.value, ErasureStatus.KEY_DISABLED.value}:
                result = await _reconcile_access_blocked(tombstone, db)
                if result:
                    inconsistencies.append(result)
                continue

            # requested / destroyed: nothing to reconcile.

    return inconsistencies


async def _main(dry_run: bool) -> int:
    inconsistencies = await reconcile(dry_run=dry_run)
    if not inconsistencies:
        print("Erasure registry reconciliation: OK, no inconsistencies found.")
        return 0

    print(f"Erasure registry reconciliation: {len(inconsistencies)} inconsistency(ies) found:")
    for item in inconsistencies:
        print(f"  - {item}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report only; do not write any state changes.")
    args = parser.parse_args()
    return asyncio.run(_main(args.dry_run))


if __name__ == "__main__":
    sys.exit(main())