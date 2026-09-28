"""Shared resilience primitives for long-running database-backed workers.

The helpers in this module deliberately keep only safe operational state:
exception *types*, failure counts, and retry delays. They never retain SQL,
payloads, credentials, patient identifiers, or exception messages.
"""

from __future__ import annotations

import asyncio
import socket
from dataclasses import dataclass

from sqlalchemy.exc import DBAPIError, OperationalError


@dataclass(slots=True)
class _WorkerHealth:
    consecutive_failures: int = 0
    degraded: bool = False
    last_error_type: str | None = None
    next_retry_seconds: float = 0.0


_HEALTH: dict[str, _WorkerHealth] = {}


def is_transient_worker_error(exc: BaseException) -> bool:
    """Return whether a worker failure is plausibly transient infrastructure loss."""

    if isinstance(
        exc,
        (
            asyncio.TimeoutError,
            TimeoutError,
            socket.gaierror,
            ConnectionAbortedError,
            ConnectionResetError,
            BrokenPipeError,
            OperationalError,
            DBAPIError,
        ),
    ):
        return True
    if isinstance(exc, OSError):
        return True

    # asyncpg connection exceptions are not guaranteed to share an OSError base.
    # Match a closed set of safe class names without importing asyncpg here.
    return type(exc).__name__ in {
        "ConnectionDoesNotExistError",
        "CannotConnectNowError",
        "ConnectionFailureError",
        "InterfaceError",
        "PostgresConnectionError",
    }


def record_worker_success(worker: str) -> None:
    state = _HEALTH.setdefault(worker, _WorkerHealth())
    state.consecutive_failures = 0
    state.degraded = False
    state.last_error_type = None
    state.next_retry_seconds = 0.0


def record_worker_failure(
    worker: str,
    exc: BaseException,
    *,
    base_delay_seconds: float = 1.0,
    max_delay_seconds: float = 30.0,
) -> tuple[float, bool]:
    """Record one failed cycle and return (retry_delay, should_log_trace)."""

    state = _HEALTH.setdefault(worker, _WorkerHealth())
    state.consecutive_failures += 1
    state.degraded = True
    state.last_error_type = type(exc).__name__

    exponent = min(state.consecutive_failures - 1, 10)
    delay = min(base_delay_seconds * (2**exponent), max_delay_seconds)
    if not is_transient_worker_error(exc):
        # Unknown failures still get bounded delay to prevent a tight log loop.
        delay = max(delay, base_delay_seconds)
    state.next_retry_seconds = float(delay)

    # First failure is visible immediately; persistent identical outages are
    # sampled at powers of two rather than emitting an unbounded stack-trace storm.
    count = state.consecutive_failures
    should_log = count == 1 or (count & (count - 1) == 0)
    return float(delay), should_log


def get_worker_runtime_health(worker: str) -> dict[str, object]:
    state = _HEALTH.setdefault(worker, _WorkerHealth())
    return {
        "degraded": state.degraded,
        "consecutive_failures": state.consecutive_failures,
        "last_error_type": state.last_error_type,
        "next_retry_seconds": state.next_retry_seconds,
    }


async def wait_for_worker_delay(stop: asyncio.Event, delay_seconds: float) -> None:
    """Sleep interruptibly so shutdown never waits for the backoff window."""

    if stop.is_set():
        return
    try:
        await asyncio.wait_for(stop.wait(), timeout=max(0.0, delay_seconds))
    except asyncio.TimeoutError:
        return
