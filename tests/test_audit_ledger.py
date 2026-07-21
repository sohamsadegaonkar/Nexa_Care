from __future__ import annotations

import asyncio
import datetime
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.core.request_context import trace_id_var
from app.observability.audit_ledger import (
    _calculate_hash,
    append_audit_log,
    append_audit_log_or_503,
)
from app.security.audit_context import AuditContext, AuditDomain


PLATFORM_CONTEXT = AuditContext.platform(domain=AuditDomain.PLATFORM)
HOSPITAL_A_CONTEXT = AuditContext.for_hospital(
    hospital_id="hospital-a", domain=AuditDomain.CONSENT
)
HOSPITAL_B_CONTEXT = AuditContext.for_hospital(
    hospital_id="hospital-b", domain=AuditDomain.CONSENT
)
PLATFORM_PARTITION = "platform:platform"


class ScalarResult:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        if self.value is None:
            raise AssertionError("scalar_one() called with no value")
        return self.value


class MappingFirstResult:
    def __init__(self, row):
        self.row = row

    def mappings(self):
        return self

    def first(self):
        return self.row


class AuditStore:
    def __init__(self):
        self.rows: list[dict] = []
        self.heads: dict[str, dict] = {}
        self.lock = asyncio.Lock()


class Connection:
    def __init__(self, store: AuditStore):
        self.store = store

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "FOR UPDATE" in sql and "audit_chain_heads" in sql:
            return MappingFirstResult(self.store.heads.get(params["chain_partition"]))
        if "idempotency_key = :idempotency_key" in sql and "audit_ledger" in sql:
            match = next(
                (
                    row["record_hash"]
                    for row in self.store.rows
                    if row.get("chain_scope") == params["chain_partition"]
                    and row.get("idempotency_key") == params["idempotency_key"]
                ),
                None,
            )
            return ScalarResult(match)
        if "INSERT INTO public.audit_ledger" in sql:
            if any(
                row["chain_scope"] == params["chain_scope"]
                and row["previous_hash"] == params["previous_hash"]
                for row in self.store.rows
            ):
                error = RuntimeError("23505 duplicate previous_hash")
                error.code = "23505"
                raise error
            row = dict(params)
            row["audit_id"] = len(self.store.rows) + 1
            row["payload"] = json.loads(row["details"])
            row["details"] = row["payload"]
            self.store.rows.append(row)
            return ScalarResult(row["audit_id"])
        if "INSERT INTO public.audit_chain_heads" in sql:
            self.store.heads[params["chain_partition"]] = {
                "chain_partition": params["chain_partition"],
                "head_event_id": params["head_event_id"],
                "head_hash": params["head_hash"],
                "sequence_number": params["sequence_number"],
                "protocol_version": params["protocol_version"],
                "is_healthy": True,
            }
            return ScalarResult()
        if "UPDATE public.audit_chain_heads" in sql and "is_healthy = FALSE" in sql:
            head = self.store.heads.get(params["chain_partition"])
            if head is not None:
                head["is_healthy"] = False
            return ScalarResult()
        if "UPDATE public.audit_chain_heads" in sql:
            head = self.store.heads[params["chain_partition"]]
            head["head_event_id"] = params["head_event_id"]
            head["head_hash"] = params["head_hash"]
            head["sequence_number"] = params["sequence_number"]
            return ScalarResult()
        raise AssertionError(sql)


class Transaction:
    def __init__(self, store: AuditStore):
        self.store = store

    async def __aenter__(self):
        await self.store.lock.acquire()
        return Connection(self.store)

    async def __aexit__(self, exc_type, exc, traceback):
        self.store.lock.release()
        return False


class Engine:
    def __init__(self, store: AuditStore):
        self.store = store

    def begin(self):
        return Transaction(self.store)


def test_calculate_hash_is_deterministic_and_chained():
    payload = {"status": "OK", "event": "X"}
    assert _calculate_hash(payload, "GENESIS") == _calculate_hash(payload, "GENESIS")
    assert _calculate_hash(payload, "GENESIS") != _calculate_hash(payload, "other")
    assert len(_calculate_hash(payload, "GENESIS")) == 64


@pytest.mark.asyncio
async def test_first_and_second_events_form_genesis_chain():
    store = AuditStore()
    token = trace_id_var.set("trace-test")
    try:
        with patch(
            "app.observability.audit_ledger.get_async_engine",
            return_value=Engine(store),
        ):
            assert await append_audit_log(
                audit_context=PLATFORM_CONTEXT,
                actor_uid="actor-1",
                event_type="FIRST",
                target_id="target",
                status="SUCCESS",
            )
            assert await append_audit_log(
                audit_context=PLATFORM_CONTEXT,
                actor_uid="actor-2",
                event_type="SECOND",
                target_id="target",
                status="SUCCESS",
            )
    finally:
        trace_id_var.reset(token)

    assert store.rows[0]["previous_hash"] == "GENESIS"
    assert store.rows[0]["sequence_number"] == 1
    assert isinstance(store.rows[0]["event_timestamp"], datetime.datetime)
    assert store.rows[1]["previous_hash"] == store.rows[0]["record_hash"]
    assert store.rows[1]["sequence_number"] == 2
    for row in store.rows:
        assert row["record_hash"] == _calculate_hash(
            row["payload"], row["previous_hash"]
        )
    assert store.heads[PLATFORM_PARTITION]["head_hash"] == store.rows[-1]["record_hash"]
    assert store.heads[PLATFORM_PARTITION]["sequence_number"] == 2


@pytest.mark.asyncio
async def test_concurrent_writers_do_not_fork_chain():
    store = AuditStore()
    with patch(
        "app.observability.audit_ledger.get_async_engine", return_value=Engine(store)
    ):
        results = await asyncio.gather(
            *(
                append_audit_log(
                    audit_context=PLATFORM_CONTEXT,
                    actor_uid=f"actor-{i}",
                    event_type="EVENT",
                    target_id=f"target-{i}",
                    status="SUCCESS",
                )
                for i in range(10)
            )
        )

    assert all(results)
    assert len(store.rows) == 10
    assert len({row["previous_hash"] for row in store.rows}) == 10
    assert store.rows[0]["previous_hash"] == "GENESIS"
    assert {row["sequence_number"] for row in store.rows} == set(range(1, 11))
    for previous, current in zip(store.rows, store.rows[1:]):
        assert current["previous_hash"] == previous["record_hash"]
    assert store.heads[PLATFORM_PARTITION]["sequence_number"] == 10


@pytest.mark.asyncio
async def test_two_partitions_never_block_or_interleave_sequences():
    store = AuditStore()
    with patch(
        "app.observability.audit_ledger.get_async_engine", return_value=Engine(store)
    ):
        assert await append_audit_log(
            audit_context=HOSPITAL_A_CONTEXT,
            actor_uid="actor-a",
            event_type="EVENT",
            target_id="target-a",
            status="SUCCESS",
        )
        assert await append_audit_log(
            audit_context=HOSPITAL_B_CONTEXT,
            actor_uid="actor-b",
            event_type="EVENT",
            target_id="target-b",
            status="SUCCESS",
        )
        assert await append_audit_log(
            audit_context=HOSPITAL_A_CONTEXT,
            actor_uid="actor-a2",
            event_type="EVENT",
            target_id="target-a2",
            status="SUCCESS",
        )

    a_rows = [
        r for r in store.rows if r["chain_scope"] == "hospital:hospital-a:consent"
    ]
    b_rows = [
        r for r in store.rows if r["chain_scope"] == "hospital:hospital-b:consent"
    ]
    assert [r["sequence_number"] for r in a_rows] == [1, 2]
    assert [r["sequence_number"] for r in b_rows] == [1]
    assert a_rows[1]["previous_hash"] == a_rows[0]["record_hash"]
    assert b_rows[0]["previous_hash"] == "GENESIS"


@pytest.mark.asyncio
async def test_unhealthy_partition_fails_closed_without_scanning_history():
    store = AuditStore()
    store.heads[PLATFORM_PARTITION] = {
        "chain_partition": PLATFORM_PARTITION,
        "head_event_id": 1,
        "head_hash": "deadbeef" * 8,
        "sequence_number": 1,
        "protocol_version": 2,
        "is_healthy": False,
    }
    with (
        patch(
            "app.observability.audit_ledger.get_async_engine",
            return_value=Engine(store),
        ),
        patch(
            "app.observability.audit_ledger.AUDIT_LEDGER_INTEGRITY_FAILURES"
        ) as metric,
    ):
        assert (
            await append_audit_log(
                audit_context=PLATFORM_CONTEXT,
                actor_uid="actor",
                event_type="AFTER_MARK_UNHEALTHY",
                target_id="target",
                status="SUCCESS",
            )
            is False
        )
    metric.labels.assert_called_once()
    assert len(store.rows) == 0


@pytest.mark.asyncio
async def test_verifier_marking_partition_unhealthy_blocks_subsequent_appends():
    store = AuditStore()
    with patch(
        "app.observability.audit_ledger.get_async_engine", return_value=Engine(store)
    ):
        assert await append_audit_log(
            audit_context=PLATFORM_CONTEXT,
            actor_uid="actor",
            event_type="OK_EVENT",
            target_id="target",
            status="SUCCESS",
        )
        store.heads[PLATFORM_PARTITION]["is_healthy"] = False
        assert (
            await append_audit_log(
                audit_context=PLATFORM_CONTEXT,
                actor_uid="actor",
                event_type="SHOULD_BE_REJECTED",
                target_id="target",
                status="SUCCESS",
            )
            is False
        )
    assert len(store.rows) == 1


@pytest.mark.asyncio
async def test_explicit_idempotency_key_does_not_duplicate_event():
    store = AuditStore()
    with patch(
        "app.observability.audit_ledger.get_async_engine", return_value=Engine(store)
    ):
        assert await append_audit_log(
            audit_context=PLATFORM_CONTEXT,
            actor_uid="actor",
            event_type="EVENT",
            target_id="target",
            status="SUCCESS",
            idempotency_key="request-123",
        )
        assert await append_audit_log(
            audit_context=PLATFORM_CONTEXT,
            actor_uid="actor",
            event_type="EVENT",
            target_id="target",
            status="SUCCESS",
            idempotency_key="request-123",
        )
    assert len(store.rows) == 1


@pytest.mark.asyncio
async def test_unique_previous_hash_conflict_retries_with_same_payload_identity():
    error = RuntimeError("23505 duplicate previous_hash")
    error.code = "23505"
    successful_row = {"record_hash": "ok"}
    append_once = AsyncMock(side_effect=[error, successful_row])

    with patch("app.observability.audit_ledger._append_once", append_once):
        assert await append_audit_log(
            audit_context=PLATFORM_CONTEXT,
            actor_uid="actor",
            event_type="EVENT",
            target_id="target",
            status="SUCCESS",
            event_timestamp="2026-07-13T12:00:00+00:00",
        )

    assert append_once.await_count == 2
    first = append_once.await_args_list[0].kwargs
    second = append_once.await_args_list[1].kwargs
    assert first["timestamp"] == second["timestamp"]
    assert first["trace_id"] == second["trace_id"]
    assert first["chain_partition"] == PLATFORM_PARTITION


@pytest.mark.asyncio
async def test_database_failure_fails_closed():
    with patch(
        "app.observability.audit_ledger._append_once",
        new=AsyncMock(side_effect=ConnectionError("database unavailable")),
    ):
        assert (
            await append_audit_log(
                audit_context=PLATFORM_CONTEXT,
                actor_uid="actor",
                event_type="EVENT",
                target_id="target",
                status="SUCCESS",
            )
            is False
        )


@pytest.mark.asyncio
async def test_append_or_503_aborts_when_audit_fails():
    with patch(
        "app.observability.audit_ledger.append_audit_log",
        new=AsyncMock(return_value=False),
    ):
        with pytest.raises(HTTPException) as exc:
            await append_audit_log_or_503(
                audit_context=PLATFORM_CONTEXT,
                actor_uid="actor",
                event_type="EVENT",
                target_id="target",
                status="SUCCESS",
            )
    assert exc.value.status_code == 503
