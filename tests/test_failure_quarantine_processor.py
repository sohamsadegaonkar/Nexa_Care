from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.services.background_worker_resilience import (
    get_worker_runtime_health,
    record_worker_success,
)
from app.services.failure_quarantine_processor import (
    run_failure_quarantine_processor_forever,
)


@pytest.fixture(autouse=True)
def _reset_failure_quarantine_health():
    record_worker_success("failure_quarantine")
    yield
    record_worker_success("failure_quarantine")


class SessionContext:
    def __init__(self, db: AsyncMock) -> None:
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_args):
        return None


@pytest.mark.asyncio
async def test_transient_db_failure_renews_session_then_recovers() -> None:
    worker = "failure_quarantine"
    record_worker_success(worker)
    stop = asyncio.Event()
    created: list[AsyncMock] = []

    def factory():
        db = AsyncMock()
        created.append(db)
        return SessionContext(db)

    attempts = 0

    async def escalate(_db, *, batch_size: int):
        nonlocal attempts
        attempts += 1
        assert batch_size == 25
        if attempts == 1:
            raise ConnectionAbortedError("synthetic disconnect")
        stop.set()

    with (
        patch(
            "app.services.failure_quarantine_processor.escalate_expired_failure_quarantines",
            side_effect=escalate,
        ),
        patch(
            "app.services.failure_quarantine_processor.wait_for_worker_delay",
            new=AsyncMock(),
        ),
    ):
        await run_failure_quarantine_processor_forever(
            factory, shutdown_event=stop, poll_interval_seconds=0.001
        )

    assert attempts == 2
    assert len(created) == 2
    created[0].commit.assert_not_awaited()
    created[1].commit.assert_awaited_once()
    assert get_worker_runtime_health(worker)["degraded"] is False


@pytest.mark.asyncio
async def test_repeated_transient_failures_use_bounded_backoff_without_completion() -> None:
    worker = "failure_quarantine"
    record_worker_success(worker)
    stop = asyncio.Event()
    waits: list[float] = []
    attempts = 0

    def factory():
        return SessionContext(AsyncMock())

    async def escalate(_db, *, batch_size: int):
        nonlocal attempts
        attempts += 1
        if attempts >= 5:
            stop.set()
        raise TimeoutError("synthetic timeout")

    async def wait(_stop, delay: float):
        waits.append(delay)

    with (
        patch(
            "app.services.failure_quarantine_processor.escalate_expired_failure_quarantines",
            side_effect=escalate,
        ),
        patch(
            "app.services.failure_quarantine_processor.wait_for_worker_delay",
            side_effect=wait,
        ),
    ):
        await run_failure_quarantine_processor_forever(factory, shutdown_event=stop)

    assert attempts == 5
    assert waits == [1.0, 2.0, 4.0, 8.0, 16.0]
    assert get_worker_runtime_health(worker)["degraded"] is True


@pytest.mark.asyncio
async def test_cancellation_is_not_swallowed() -> None:
    stop = asyncio.Event()
    db = AsyncMock()

    with patch(
        "app.services.failure_quarantine_processor.escalate_expired_failure_quarantines",
        new=AsyncMock(side_effect=asyncio.CancelledError()),
    ):
        with pytest.raises(asyncio.CancelledError):
            await run_failure_quarantine_processor_forever(
                lambda: SessionContext(db), shutdown_event=stop
            )

    db.commit.assert_not_awaited()
