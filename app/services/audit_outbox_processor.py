"""Background processor for the transactional audit outbox.

Authoritative flow:  policy transaction -> audit_outbox -> this processor
-> immutable audit ledger (append_audit_log). This module is the only
thing that ever appends outbox events to the ledger -- the policy
transaction itself never calls append_audit_log synchronously, so the
same logical event is never appended twice from two different places.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.observability.audit_ledger import append_audit_log_for_stored_partition
from app.observability.safe_exceptions import log_safe_exception

logger = logging.getLogger("nexa_logger")

DEFAULT_BATCH_SIZE = 25
DEFAULT_MAX_ATTEMPTS = 8
DEFAULT_LEASE_SECONDS = 60
_BACKOFF_BASE_SECONDS = 5
_BACKOFF_MAX_SECONDS = 900  # 15 minutes

_CLAIM_SQL = text(
    """
    WITH claimable AS (
        SELECT id
        FROM public.audit_outbox
        WHERE (status = 'pending' AND available_at <= now())
           OR (status = 'processing' AND lease_expires_at < now())
        ORDER BY COALESCE(lease_expires_at, available_at) ASC
        FOR UPDATE SKIP LOCKED
        LIMIT :batch_size
    )
    UPDATE public.audit_outbox AS outbox
    SET status = 'processing',
        processing_started_at = now(),
        lease_expires_at = now() + make_interval(secs => :lease_seconds),
        worker_id = :worker_id
    FROM claimable
    WHERE outbox.id = claimable.id
    RETURNING outbox.id, outbox.event_id, outbox.idempotency_key,
              outbox.chain_partition, outbox.event_type, outbox.actor_id,
              outbox.tenant_id, outbox.patient_id, outbox.payload,
              outbox.attempt_count
    """
)
_MARK_PROCESSED_SQL = text(
    """UPDATE public.audit_outbox
       SET status = 'processed', processed_at = now(),
           processing_started_at = NULL, lease_expires_at = NULL, worker_id = NULL
       WHERE id = :id"""
)
_MARK_RETRY_SQL = text(
    """
    UPDATE public.audit_outbox
    SET status = 'pending', attempt_count = attempt_count + 1,
        available_at = :available_at, last_error_code = :error_code,
        processing_started_at = NULL, lease_expires_at = NULL, worker_id = NULL
    WHERE id = :id
    """
)
_MARK_DEAD_LETTER_SQL = text(
    """
    UPDATE public.audit_outbox
    SET status = 'dead_letter', attempt_count = attempt_count + 1, last_error_code = :error_code,
        processing_started_at = NULL, lease_expires_at = NULL, worker_id = NULL
    WHERE id = :id
    """
)

_HEALTH_SQL = text(
    """
    SELECT
        count(*) FILTER (WHERE status = 'pending') AS pending_count,
        count(*) FILTER (WHERE status = 'dead_letter') AS dead_letter_backlog,
        count(*) FILTER (WHERE status = 'processing' AND lease_expires_at < now()) AS expired_lease_count,
        COALESCE(EXTRACT(EPOCH FROM (now() - min(available_at) FILTER (WHERE status = 'pending'))), 0)
            AS oldest_pending_age_seconds,
        COALESCE(EXTRACT(EPOCH FROM (now() - min(lease_expires_at) FILTER (
            WHERE status = 'processing' AND lease_expires_at < now()
        ))), 0) AS oldest_expired_lease_age_seconds
    FROM public.audit_outbox
    """
)


def _backoff_seconds(attempt_count: int) -> int:
    return min(
        _BACKOFF_BASE_SECONDS * (2 ** max(0, attempt_count)), _BACKOFF_MAX_SECONDS
    )


def make_worker_id() -> str:
    """Create a process-unique lease owner identifier without credentials."""
    return f"{socket.gethostname()[:48]}:{os.getpid()}:{uuid.uuid4().hex[:12]}"


async def get_outbox_health(db: AsyncSession) -> dict[str, int | float]:
    """Return safe aggregate readiness data; never expose outbox payloads."""
    result = await db.execute(_HEALTH_SQL)
    row = result.mappings().one()
    return {
        "pending_count": int(row["pending_count"] or 0),
        "dead_letter_backlog": int(row["dead_letter_backlog"] or 0),
        "expired_lease_count": int(row["expired_lease_count"] or 0),
        "oldest_pending_age_seconds": float(row["oldest_pending_age_seconds"] or 0),
        "oldest_expired_lease_age_seconds": float(
            row["oldest_expired_lease_age_seconds"] or 0
        ),
    }


async def process_outbox_batch(
    db: AsyncSession,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    worker_id: str | None = None,
) -> dict[str, int]:
    """Claim and process one batch. Safe to run concurrently across multiple
    app instances -- FOR UPDATE SKIP LOCKED means two workers never claim
    the same row."""

    owner = worker_id or make_worker_id()
    result = await db.execute(
        _CLAIM_SQL,
        {"batch_size": batch_size, "lease_seconds": lease_seconds, "worker_id": owner},
    )
    rows = result.mappings().all()
    if not rows:
        await db.commit()
        return {"claimed": 0, "processed": 0, "retried": 0, "dead_lettered": 0}

    await db.commit()

    processed = 0
    retried = 0
    dead_lettered = 0

    for row in rows:
        try:
            success = await append_audit_log_for_stored_partition(
                actor_uid=row["actor_id"],
                event_type=row["event_type"],
                target_id=row["patient_id"] or row["tenant_id"] or str(row["event_id"]),
                status="SUCCESS",
                metadata=row["payload"] if isinstance(row["payload"], dict) else None,
                idempotency_key=row["idempotency_key"],
                stored_partition=row["chain_partition"],
            )
            if not success:
                raise RuntimeError("append_audit_log returned False")

            await db.execute(_MARK_PROCESSED_SQL, {"id": row["id"]})
            await db.commit()
            processed += 1
        except Exception as exc:  # noqa: BLE001 - must survive individual failures
            log_safe_exception(
                logger,
                logging.ERROR,
                "audit_outbox_event_failed",
                exc,
                subsystem="audit_outbox",
                operation="process_outbox_batch",
                fields={
                    "outbox_id": str(row["id"]),
                    "attempt_count": row["attempt_count"],
                },
            )
            error_code = type(exc).__name__[:64]
            if row["attempt_count"] + 1 >= max_attempts:
                await db.execute(
                    _MARK_DEAD_LETTER_SQL, {"id": row["id"], "error_code": error_code}
                )
                dead_lettered += 1
            else:
                available_at = datetime.now(timezone.utc) + timedelta(
                    seconds=_backoff_seconds(row["attempt_count"])
                )
                await db.execute(
                    _MARK_RETRY_SQL,
                    {
                        "id": row["id"],
                        "available_at": available_at,
                        "error_code": error_code,
                    },
                )
                retried += 1
            await db.commit()

    return {
        "claimed": len(rows),
        "processed": processed,
        "retried": retried,
        "dead_lettered": dead_lettered,
    }


async def run_outbox_processor_forever(
    session_factory,
    *,
    poll_interval_seconds: float = 2.0,
    shutdown_event: asyncio.Event | None = None,
) -> None:
    """Run process_outbox_batch in a loop until shutdown_event is set.

    session_factory is any zero-arg callable returning a fresh AsyncSession
    (e.g. app.core.database.get_db_session_factory()), so each batch gets
    its own transaction and a crash mid-batch never wedges the connection.
    """

    stop = shutdown_event or asyncio.Event()
    worker_id = make_worker_id()
    while not stop.is_set():
        try:
            async with session_factory() as db:
                await process_outbox_batch(db, worker_id=worker_id)
        except Exception as exc:  # noqa: BLE001 - the loop itself must survive
            log_safe_exception(
                logger,
                logging.ERROR,
                "audit_outbox_processor_loop_failed",
                exc,
                subsystem="audit_outbox",
                operation="run_outbox_processor_forever",
            )
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_interval_seconds)
        except asyncio.TimeoutError:
            pass
