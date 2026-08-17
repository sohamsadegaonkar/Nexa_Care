"""Long-running processor for failure-quarantine escalation only."""

from __future__ import annotations

import asyncio
import logging

from app.observability.safe_exceptions import log_safe_exception
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
        try:
            async with session_factory() as db:
                await escalate_expired_failure_quarantines(db, batch_size=batch_size)
                await db.commit()
        except Exception as exc:  # noqa: BLE001 - worker must survive a bad cycle
            log_safe_exception(
                logger,
                logging.ERROR,
                "failure_quarantine_processor_cycle_failed",
                exc,
                subsystem="failure_quarantine",
                operation="run_failure_quarantine_processor_forever",
            )
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_interval_seconds)
        except asyncio.TimeoutError:
            pass
