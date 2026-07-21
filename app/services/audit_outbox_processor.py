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
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.observability.audit_ledger import append_audit_log
from app.observability.safe_exceptions import log_safe_exception

logger = logging.getLogger("nexa_logger")

DEFAULT_BATCH_SIZE = 25
DEFAULT_MAX_ATTEMPTS = 8
_BACKOFF_BASE_SECONDS = 5
_BACKOFF_MAX_SECONDS = 900  # 15 minutes

_CLAIM_SQL = text(
    """
    SELECT id, event_id, idempotency_key, chain_partition, event_type,
           actor_id, tenant_id, patient_id, payload, attempt_count
    FROM public.audit_outbox
    WHERE status = 'pending' AND available_at <= now()
    ORDER BY available_at ASC
    FOR UPDATE SKIP LOCKED
    LIMIT :batch_size
    """
)
_MARK_PROCESSING_SQL = text(
    "UPDATE public.audit_outbox SET status = 'processing' WHERE id = ANY(:ids)"
)
_MARK_PROCESSED_SQL = text(
    "UPDATE public.audit_outbox SET status = 'processed', processed_at = now() WHERE id = :id"
)
_MARK_RETRY_SQL = text(
    """
    UPDATE public.audit_outbox
    SET status = 'pending', attempt_count = attempt_count + 1,
        available_at = :available_at, last_error_code = :error_code
    WHERE id = :id
    """
)
_MARK_DEAD_LETTER_SQL = text(
    """
    UPDATE public.audit_outbox
    SET status = 'dead_letter', attempt_count = attempt_count + 1, last_error_code = :error_code
    WHERE id = :id
    """
)

_HEALTH_SQL = text(
    """
    SELECT
        count(*) FILTER (WHERE status = 'dead_letter') AS dead_letter_backlog,
        count(*) FILTER (
            WHERE status = 'pending'
              AND available_at < now() - interval '5 minutes'
        ) AS stalled_pending_events
    FROM public.audit_outbox
    """
)


def _backoff_seconds(attempt_count: int) -> int:
    return min(_BACKOFF_BASE_SECONDS * (2 ** max(0, attempt_count)), _BACKOFF_MAX_SECONDS)


async def get_outbox_health(db: AsyncSession) -> dict[str, int]:
    """Return safe aggregate readiness data; never expose outbox payloads."""
    result = await db.execute(_HEALTH_SQL)
    row = result.mappings().one()
    return {
        "dead_letter_backlog": int(row["dead_letter_backlog"] or 0),
        "stalled_pending_events": int(row["stalled_pending_events"] or 0),
    }


async def process_outbox_batch(
    db: AsyncSession, *, batch_size: int = DEFAULT_BATCH_SIZE, max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> dict[str, int]:
    """Claim and process one batch. Safe to run concurrently across multiple
    app instances -- FOR UPDATE SKIP LOCKED means two workers never claim
    the same row."""

    result = await db.execute(_CLAIM_SQL, {"batch_size": batch_size})
    rows = result.mappings().all()
    if not rows:
        await db.commit()
        return {"claimed": 0, "processed": 0, "retried": 0, "dead_lettered": 0}

    ids = [row["id"] for row in rows]
    await db.execute(_MARK_PROCESSING_SQL, {"ids": ids})
    await db.commit()

    processed = 0
    retried = 0
    dead_lettered = 0

    for row in rows:
        try:
            success = await append_audit_log(
                actor_uid=row["actor_id"],
                event_type=row["event_type"],
                target_id=row["patient_id"] or row["tenant_id"] or str(row["event_id"]),
                status="SUCCESS",
                metadata=row["payload"] if isinstance(row["payload"], dict) else None,
                idempotency_key=row["idempotency_key"],
                chain_partition=row["chain_partition"],
            )
            if not success:
                raise RuntimeError("append_audit_log returned False")

            await db.execute(_MARK_PROCESSED_SQL, {"id": row["id"]})
            await db.commit()
            processed += 1
        except Exception as exc:  # noqa: BLE001 - must survive individual failures
            log_safe_exception(
                logger, logging.ERROR, "audit_outbox_event_failed", exc,
                subsystem="audit_outbox", operation="process_outbox_batch",
                fields={"outbox_id": str(row["id"]), "attempt_count": row["attempt_count"]},
            )
            error_code = type(exc).__name__[:64]
            if row["attempt_count"] + 1 >= max_attempts:
                await db.execute(_MARK_DEAD_LETTER_SQL, {"id": row["id"], "error_code": error_code})
                dead_lettered += 1
            else:
                available_at = datetime.now(timezone.utc) + timedelta(seconds=_backoff_seconds(row["attempt_count"]))
                await db.execute(
                    _MARK_RETRY_SQL,
                    {"id": row["id"], "available_at": available_at, "error_code": error_code},
                )
                retried += 1
            await db.commit()

    return {"claimed": len(rows), "processed": processed, "retried": retried, "dead_lettered": dead_lettered}


async def run_outbox_processor_forever(
    session_factory, *, poll_interval_seconds: float = 2.0, shutdown_event: asyncio.Event | None = None,
) -> None:
    """Run process_outbox_batch in a loop until shutdown_event is set.

    session_factory is any zero-arg callable returning a fresh AsyncSession
    (e.g. app.core.database.get_db_session_factory()), so each batch gets
    its own transaction and a crash mid-batch never wedges the connection.
    """

    stop = shutdown_event or asyncio.Event()
    while not stop.is_set():
        try:
            async with session_factory() as db:
                await process_outbox_batch(db)
        except Exception as exc:  # noqa: BLE001 - the loop itself must survive
            log_safe_exception(
                logger, logging.ERROR, "audit_outbox_processor_loop_failed", exc,
                subsystem="audit_outbox", operation="run_outbox_processor_forever",
            )
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_interval_seconds)
        except asyncio.TimeoutError:
            pass
