"""Fail-closed erasure-registry gate.

Every local and AWS decrypt path calls check_erasure_registry() before
returning a cached or freshly-unwrapped DEK. The contract is strict:

    registry verified, no tombstone         -> continue
    active tombstone                         -> deny (PatientDataErased)
    registry unavailable / malformed / error -> deny (ErasureRegistryUnavailable)

A registry error is NEVER treated as "not erased" -- that would silently
reopen access to a patient who requested erasure the moment the registry
had a bad day. Both failure modes deny access; only the first two rows
differ in *why* they deny it.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.erasure_tombstone import ErasureStatus, PatientErasureTombstone
from app.observability.safe_exceptions import log_safe_exception

logger = logging.getLogger("nexa_logger")

# Statuses that mean "application access to this patient's data must be denied".
_ACTIVE_TOMBSTONE_STATUSES = {
    ErasureStatus.REQUESTED.value,
    ErasureStatus.ACCESS_BLOCKED.value,
    ErasureStatus.KEY_DISABLED.value,
    ErasureStatus.DELETION_SCHEDULED.value,
    ErasureStatus.DESTROYED.value,
    ErasureStatus.OPERATOR_ACTION_REQUIRED.value,
}


class ErasureRegistryUnavailable(RuntimeError):
    """The registry could not be queried or returned malformed data.
    Callers must treat this exactly like an active tombstone: deny access.
    """


async def check_erasure_registry(patient_ref: str, db: AsyncSession) -> None:
    """Raise if `patient_ref` has an active erasure tombstone, or if the
    registry itself could not be safely queried. Returns None (silently)
    only when the registry was reachable and confirmed no tombstone exists.
    """

    try:
        result = await db.execute(
            select(PatientErasureTombstone.status).where(PatientErasureTombstone.patient_ref == str(patient_ref))
        )
        status = result.scalar_one_or_none()
    except Exception as exc:
        log_safe_exception(
            logger, logging.ERROR, "erasure_registry_query_failed", exc,
            subsystem="database", operation="check_erasure_registry",
        )
        raise ErasureRegistryUnavailable("Erasure registry query failed; failing closed.") from exc

    if status is None:
        return  # No tombstone on record -- access proceeds.

    if status not in _ACTIVE_TOMBSTONE_STATUSES:
        # Defensive: an unrecognized status value is malformed data, not
        # "no tombstone". Fail closed rather than guessing.
        raise ErasureRegistryUnavailable(f"Erasure registry returned an unrecognized status: {status!r}")

    raise _PatientErasedSignal(patient_ref)


class _PatientErasedSignal(RuntimeError):
    """Internal signal raised on an active tombstone; crypto_kms.py catches
    this and re-raises its own PatientDataErased so all existing callers
    keep working against one exception type."""


async def create_tombstone(
    db: AsyncSession, *, patient_ref: str, tenant_id: str | None,
    wrapping_key_type: str, patient_wrapping_key_id: str | None = None,
) -> PatientErasureTombstone:
    existing = await db.execute(
        select(PatientErasureTombstone).where(PatientErasureTombstone.patient_ref == str(patient_ref))
    )
    row = existing.scalar_one_or_none()
    if row is not None:
        return row  # Idempotent: repeated erasure requests return the existing tombstone.

    row = PatientErasureTombstone(
        tenant_id=tenant_id,
        patient_ref=str(patient_ref),
        status=ErasureStatus.REQUESTED.value,
        assurance_level="active_access_blocked",
        wrapping_key_type=wrapping_key_type,
        patient_wrapping_key_id=patient_wrapping_key_id,
        requested_at=datetime.now(timezone.utc),
        operator_action_required=False,
        retry_required=False,
    )
    db.add(row)
    await db.flush()
    return row


async def mark_access_blocked(db: AsyncSession, tombstone: PatientErasureTombstone) -> None:
    tombstone.status = ErasureStatus.ACCESS_BLOCKED.value
    tombstone.assurance_level = "active_access_blocked"
    tombstone.effective_at = datetime.now(timezone.utc)
    tombstone.updated_at = datetime.now(timezone.utc)
    await db.flush()


async def mark_key_disabled(db: AsyncSession, tombstone: PatientErasureTombstone, kms_state: str | None) -> None:
    tombstone.status = ErasureStatus.KEY_DISABLED.value
    tombstone.kms_state = kms_state
    tombstone.updated_at = datetime.now(timezone.utc)
    await db.flush()


async def mark_deletion_scheduled(
    db: AsyncSession, tombstone: PatientErasureTombstone, scheduled_deletion_date: datetime,
) -> None:
    tombstone.status = ErasureStatus.DELETION_SCHEDULED.value
    tombstone.assurance_level = "patient_key_deletion_scheduled"
    tombstone.scheduled_deletion_date = scheduled_deletion_date
    tombstone.updated_at = datetime.now(timezone.utc)
    await db.flush()


async def mark_destroyed(db: AsyncSession, tombstone: PatientErasureTombstone) -> None:
    tombstone.status = ErasureStatus.DESTROYED.value
    tombstone.assurance_level = "patient_key_destroyed"
    tombstone.completion_date = datetime.now(timezone.utc)
    tombstone.updated_at = datetime.now(timezone.utc)
    await db.flush()


async def mark_operator_action_required(
    db: AsyncSession, tombstone: PatientErasureTombstone, *, failure_code: str, retry_required: bool = True,
) -> None:
    """KMS disable/schedule-deletion failed. The tombstone is preserved,
    application access stays blocked (status is NOT reverted), and this is
    flagged for an operator/outbox retry -- never silently reported as
    'erased'."""
    tombstone.status = ErasureStatus.OPERATOR_ACTION_REQUIRED.value
    tombstone.failure_code = failure_code
    tombstone.operator_action_required = True
    tombstone.retry_required = retry_required
    tombstone.updated_at = datetime.now(timezone.utc)
    await db.flush()