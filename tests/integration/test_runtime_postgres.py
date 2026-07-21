"""Real PostgreSQL contracts for the final runtime correction.

These tests intentionally skip unless TEST_DATABASE_URL identifies a disposable
database. They use real transactions and PostgreSQL locks, never AsyncMock.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.services.audit_outbox_processor import _CLAIM_SQL


pytestmark = pytest.mark.postgres


@pytest.fixture
async def postgres_engine():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    engine = create_async_engine(url)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_uuid_audit_head_schema_and_foreign_key(postgres_engine):
    async with postgres_engine.connect() as connection:
        types = dict((await connection.execute(text(
            """
            SELECT table_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND (table_name, column_name) IN (
                  ('audit_ledger', 'audit_id'),
                  ('audit_chain_heads', 'head_event_id')
              )
            """
        ))).all())
        assert types == {"audit_ledger": "uuid", "audit_chain_heads": "uuid"}

        foreign_key = (await connection.execute(text(
            """
            SELECT ccu.table_name, ccu.column_name, rc.delete_rule
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name AND tc.constraint_schema = kcu.constraint_schema
            JOIN information_schema.constraint_column_usage ccu
              ON tc.constraint_name = ccu.constraint_name AND tc.constraint_schema = ccu.constraint_schema
            JOIN information_schema.referential_constraints rc
              ON tc.constraint_name = rc.constraint_name AND tc.constraint_schema = rc.constraint_schema
            WHERE tc.constraint_schema = 'public'
              AND tc.table_name = 'audit_chain_heads'
              AND kcu.column_name = 'head_event_id'
            """
        ))).one()
        assert tuple(foreign_key) == ("audit_ledger", "audit_id", "RESTRICT")


@pytest.mark.asyncio
async def test_outbox_active_lease_is_not_double_claimed(postgres_engine):
    outbox_id = uuid.uuid4()
    params = {
        "id": outbox_id,
        "idempotency_key": f"postgres-lease-{uuid.uuid4().hex}",
        "partition": f"tenant:{uuid.uuid4()}:policy",
    }
    async with postgres_engine.begin() as connection:
        await connection.execute(text(
            """
            INSERT INTO public.audit_outbox
                (id, event_id, idempotency_key, chain_partition, event_type, actor_id,
                 tenant_id, patient_id, payload, status, attempt_count, available_at, created_at)
            VALUES
                (:id, gen_random_uuid(), :idempotency_key, :partition, 'POSTGRES_LEASE_TEST',
                 'test-worker', 'test-tenant', NULL, '{}'::jsonb, 'pending', 0, now(), now())
            """
        ), params)
    try:
        async with postgres_engine.begin() as first:
            first_rows = (await first.execute(_CLAIM_SQL, {
                "batch_size": 1, "lease_seconds": 60, "worker_id": "worker-one",
            })).mappings().all()
        async with postgres_engine.begin() as second:
            second_rows = (await second.execute(_CLAIM_SQL, {
                "batch_size": 1, "lease_seconds": 60, "worker_id": "worker-two",
            })).mappings().all()
        assert [row["id"] for row in first_rows] == [outbox_id]
        assert all(row["id"] != outbox_id for row in second_rows)
    finally:
        async with postgres_engine.begin() as connection:
            await connection.execute(text("DELETE FROM public.audit_outbox WHERE id = :id"), {"id": outbox_id})


@pytest.mark.asyncio
async def test_expired_outbox_lease_is_reclaimed(postgres_engine):
    outbox_id = uuid.uuid4()
    async with postgres_engine.begin() as connection:
        await connection.execute(text(
            """
            INSERT INTO public.audit_outbox
                (id, event_id, idempotency_key, chain_partition, event_type, actor_id,
                 tenant_id, payload, status, attempt_count, available_at, created_at,
                 processing_started_at, lease_expires_at, worker_id)
            VALUES
                (:id, gen_random_uuid(), :key, 'tenant:test:policy', 'POSTGRES_RECLAIM_TEST',
                 'test-worker', 'test-tenant', '{}'::jsonb, 'processing', 0, now(), now(),
                 now() - interval '2 minutes', now() - interval '1 minute', 'crashed-worker')
            """
        ), {"id": outbox_id, "key": f"postgres-reclaim-{uuid.uuid4().hex}"})
    try:
        async with postgres_engine.begin() as connection:
            rows = (await connection.execute(_CLAIM_SQL, {
                "batch_size": 1, "lease_seconds": 60, "worker_id": "replacement-worker",
            })).mappings().all()
        assert [row["id"] for row in rows] == [outbox_id]
    finally:
        async with postgres_engine.begin() as connection:
            await connection.execute(text("DELETE FROM public.audit_outbox WHERE id = :id"), {"id": outbox_id})
