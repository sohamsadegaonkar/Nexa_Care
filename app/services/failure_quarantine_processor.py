"""Long-running processor for failure-quarantine escalation only."""

from __future__ import annotations

import asyncio
import logging

from app.observability.safe_exceptions import log_safe_exception
from app.services.background_worker_resilience import (
    record_worker_failure,
    record_worker_success,
    wait_for_worker_delay,
)
from app.services.failure_quarantine import escalate_expired_failure_quarantines

logger = logging.getLogger("nexa_logger")


async def run_failure_quarantine_processor_forever(
    session_factory,
    *,
    poll_interval_seconds: float = 2.0,
    batch_size: int = 25,
    shutdown_event: asyncio.Event | None = None,
) -> None:
    """Escalate due cases until stopped; never access source or clinical state."""
    stop = shutdown_event or asyncio.Event()
    while not stop.is_set():
        delay_seconds = poll_interval_seconds
        try:
            # Renew the session on every cycle so a severed pool connection is
            # discarded instead of being reused indefinitely.
            async with session_factory() as db:
                await escalate_expired_failure_quarantines(db, batch_size=batch_size)
                await db.commit()
            record_worker_success("failure_quarantine")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - worker must survive a bad cycle
            delay_seconds, should_log = record_worker_failure(
                "failure_quarantine", exc
            )
            if should_log:
                log_safe_exception(
                    logger,
                    logging.WARNING,
                    "failure_quarantine_processor_transient_failure",
                    exc,
                    subsystem="failure_quarantine",
                    operation="run_failure_quarantine_processor_forever",
                    fields={"retry_delay_seconds": delay_seconds},
                )
        await wait_for_worker_delay(stop, delay_seconds)
