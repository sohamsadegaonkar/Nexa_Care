from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException

import app.main as main
from app.core.production_runtime import RuntimePreflightError
from app.services.crypto_kms import PatientDataErased


class _RunningTask:
    def done(self) -> bool:
        return False


class _FailingRedis:
    async def ping(self) -> bool:
        raise RuntimeError("redis-sensitive-detail")


class _FailingConnectionContext:
    async def __aenter__(self):
        raise RuntimeError("postgres-sensitive-detail")

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _FailingEngine:
    def connect(self) -> _FailingConnectionContext:
        return _FailingConnectionContext()


def test_public_readiness_is_coarse_when_dependencies_fail(monkeypatch) -> None:
    monkeypatch.setattr(main, "get_async_redis_client", lambda: _FailingRedis())
    monkeypatch.setattr(main, "get_async_engine", lambda: _FailingEngine())
    monkeypatch.setattr(main.app.state, "audit_outbox_task", _RunningTask(), raising=False)
    monkeypatch.setattr(main.app.state, "failure_quarantine_task", None, raising=False)
    monkeypatch.setattr(
        main.app.state, "provider_reconciliation_required", False, raising=False
    )
    monkeypatch.setattr(
        main.app.state, "provider_reconciliation_task", None, raising=False
    )

    payload = asyncio.run(main._readiness_snapshot(detailed=False, include_aws=False))

    assert payload["status"] == "degraded"
    assert payload["checks"]["redis"] == "unavailable"
    assert payload["checks"]["postgres"] == "unavailable"
    serialized = json.dumps(payload)
    assert "RuntimeError" not in serialized
    assert "sensitive-detail" not in serialized
    assert "pending_count" not in serialized


def test_operations_endpoints_require_independent_token_in_production(
    monkeypatch,
) -> None:
    token = "operations-token-" + "x" * 32
    monkeypatch.setenv("ENVIRONMENT", "pilot")
    monkeypatch.setenv("ENV", "pilot")
    monkeypatch.setenv("OPERATIONS_AUTH_TOKEN", token)

    rejected = SimpleNamespace(headers={"x-nexa-operations-token": "wrong"})
    with pytest.raises(HTTPException) as error:
        main._operations_access(rejected)
    assert error.value.status_code == 403
    assert error.value.detail == "Forbidden"

    accepted = SimpleNamespace(headers={"x-nexa-operations-token": token})
    main._operations_access(accepted)


def test_preflight_failure_occurs_before_any_background_worker_starts(
    monkeypatch,
) -> None:
    monkeypatch.setattr(main, "get_supabase_config", lambda: object())
    monkeypatch.setattr(main, "get_redis_config", lambda: object())
    monkeypatch.setattr(main, "get_handshake_config", lambda: object())
    monkeypatch.setattr(main, "get_database_config", lambda: object())
    monkeypatch.setattr(
        main,
        "get_document_extraction_config",
        lambda: SimpleNamespace(async_multipage_enabled=False),
    )
    monkeypatch.setattr(
        main,
        "get_document_storage_config",
        lambda: SimpleNamespace(provider="s3"),
    )
    monkeypatch.setattr(main, "get_encryption_provider", lambda: object())
    monkeypatch.setattr(main, "trusted_proxy_networks", lambda: ())

    async def fail_preflight():
        raise RuntimePreflightError("SCHEMA_REVISION_MISMATCH")

    outbox = AsyncMock()
    quarantine = AsyncMock()
    reconciliation = AsyncMock()
    monkeypatch.setattr(main, "run_production_startup_preflight", fail_preflight)
    monkeypatch.setattr(main, "run_outbox_processor_forever", outbox)
    monkeypatch.setattr(main, "run_failure_quarantine_processor_forever", quarantine)
    monkeypatch.setattr(
        main, "run_provider_job_reconciliation_processor_forever", reconciliation
    )

    async def exercise() -> None:
        with pytest.raises(RuntimePreflightError, match="SCHEMA_REVISION_MISMATCH"):
            async with main.lifespan(FastAPI()):
                pytest.fail("lifespan must not yield after failed preflight")

    asyncio.run(exercise())
    outbox.assert_not_awaited()
    quarantine.assert_not_awaited()
    reconciliation.assert_not_awaited()


def test_worker_supervisor_restarts_one_unexpected_exit_then_stops_cleanly() -> None:
    shutdown = asyncio.Event()
    attempts = 0

    async def worker() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("worker-sensitive-detail")
        shutdown.set()

    asyncio.run(
        asyncio.wait_for(
            main._supervise_worker("qualification-worker", worker, shutdown),
            timeout=2.0,
        )
    )
    assert attempts == 2


def test_worker_status_marks_exited_supervisor_unavailable() -> None:
    class _ExitedTask:
        def done(self) -> bool:
            return True

    assert main._worker_status(_RunningTask()) == "ok"
    assert main._worker_status(_ExitedTask()) == "unavailable"
    assert main._worker_status(None) == "unavailable"


def test_global_erasure_response_does_not_disclose_patient_identifier() -> None:
    sensitive_patient_id = "patient-sensitive-id"
    response = asyncio.run(
        main.patient_data_erased_handler(None, PatientDataErased(sensitive_patient_id))
    )
    body = json.loads(response.body)
    assert response.status_code == 410
    assert body["error_code"] == "PATIENT_DATA_ERASED"
    assert sensitive_patient_id not in json.dumps(body)
