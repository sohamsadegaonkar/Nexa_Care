from __future__ import annotations

import asyncio
import socket

import pytest
from sqlalchemy.exc import OperationalError

from app.services.background_worker_resilience import (
    get_worker_runtime_health,
    is_transient_worker_error,
    record_worker_failure,
    record_worker_success,
    wait_for_worker_delay,
)


@pytest.mark.parametrize(
    "exc",
    [
        TimeoutError(),
        asyncio.TimeoutError(),
        socket.gaierror(),
        ConnectionAbortedError(),
        ConnectionResetError(),
        OperationalError("select 1", {}, RuntimeError("db disconnected")),
    ],
)
def test_transient_worker_error_classification(exc: BaseException) -> None:
    assert is_transient_worker_error(exc) is True


def test_backoff_is_bounded_and_success_resets_degraded_state() -> None:
    worker = "test-bounded-worker"
    delays = [
        record_worker_failure(worker, ConnectionResetError())[0] for _ in range(8)
    ]
    assert delays[:5] == [1.0, 2.0, 4.0, 8.0, 16.0]
    assert all(delay <= 30.0 for delay in delays)
    health = get_worker_runtime_health(worker)
    assert health["degraded"] is True
    assert health["consecutive_failures"] == 8
    assert health["last_error_type"] == "ConnectionResetError"

    record_worker_success(worker)
    health = get_worker_runtime_health(worker)
    assert health == {
        "degraded": False,
        "consecutive_failures": 0,
        "last_error_type": None,
        "next_retry_seconds": 0.0,
    }


def test_repeated_failure_logging_is_rate_sampled() -> None:
    worker = "test-log-sampling-worker"
    should_log = [
        record_worker_failure(worker, TimeoutError())[1] for _ in range(8)
    ]
    assert should_log == [True, True, False, True, False, False, False, True]


@pytest.mark.asyncio
async def test_worker_delay_is_interruptible_by_shutdown() -> None:
    stop = asyncio.Event()

    async def trigger_shutdown() -> None:
        await asyncio.sleep(0)
        stop.set()

    trigger = asyncio.create_task(trigger_shutdown())
    await wait_for_worker_delay(stop, 30.0)
    await trigger
    assert stop.is_set()


@pytest.mark.asyncio
async def test_worker_delay_propagates_cancellation() -> None:
    stop = asyncio.Event()
    task = asyncio.create_task(wait_for_worker_delay(stop, 30.0))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
