"""Non-NFC development preflight regression: no authorization or physical NFC bypass."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import sqlalchemy.ext.asyncio as sa_async

from scripts import demo_preflight as preflight


def test_cli_discovery_mode_is_explicit_and_restricted():
    assert preflight._parse_discovery_mode([]) == "nfc"
    assert preflight._parse_discovery_mode(["--discovery", "public-id"]) == "public-id"
    with pytest.raises(SystemExit):
        preflight._parse_discovery_mode(["--discovery", "phone"])
    with pytest.raises(SystemExit):
        preflight._parse_discovery_mode(["--discovery", "skip-consent"])


class _FakeDB:
    def __init__(self):
        self.scalar = AsyncMock(side_effect=[SimpleNamespace(id=uuid4()), uuid4(), None])
        self.get = AsyncMock(
            side_effect=lambda _model, patient_id: SimpleNamespace(
                is_deleted=False,
                public_patient_id=preflight.demo_public_patient_id(patient_id),
            )
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


@pytest.mark.asyncio
async def test_public_id_requires_clinical_eligibility_and_erasure_but_not_nfc_registry(
    monkeypatch,
):
    db = _FakeDB()
    engine = SimpleNamespace(dispose=AsyncMock())
    eligibility = AsyncMock(return_value=SimpleNamespace(allowed=True))
    erasure = AsyncMock()
    monkeypatch.setattr(preflight, "create_async_engine", lambda _url: engine)
    monkeypatch.setattr(sa_async, "async_sessionmaker", lambda *_a, **_kw: lambda: db)
    monkeypatch.setattr(
        preflight,
        "ClinicalEligibilityService",
        lambda **_kw: SimpleNamespace(evaluate_interactive=eligibility),
    )
    monkeypatch.setattr(preflight, "check_erasure_registry", erasure)

    ok, reason = await preflight._demo_state("postgresql+asyncpg://localhost/demo", discovery_mode="public-id")
    assert (ok, reason) == (True, None)
    assert db.scalar.await_count == 2  # provider and hospital, no NFC card query
    assert db.get.await_count == 2  # both synthetic patients, correct public IDs
    assert erasure.await_count == 2
    assert eligibility.await_count == 8  # all provider capabilities still required
    engine.dispose.assert_awaited_once()

    db.scalar.reset_mock(side_effect=True)
    db.scalar.side_effect = [SimpleNamespace(id=uuid4()), uuid4(), None]
    ok, reason = await preflight._demo_state("postgresql+asyncpg://localhost/demo", discovery_mode="nfc")
    assert (ok, reason) == (False, "DEMO_NFC_NOT_READY")
    assert db.scalar.await_count == 3  # existing NFC registry check unchanged


@pytest.mark.asyncio
async def test_public_id_ready_is_not_physical_nfc_or_release_go(monkeypatch, capsys):
    from scripts import demo_preflight as m
    client = SimpleNamespace(ping=AsyncMock(return_value=True), aclose=AsyncMock())
    monkeypatch.setattr(m, "require_demo_environment", lambda _: "development")
    monkeypatch.setattr(
        m, "get_database_config",
        lambda: SimpleNamespace(url="postgresql+asyncpg://localhost/demo"),
    )
    monkeypatch.setattr(
        m, "get_redis_config",
        lambda: SimpleNamespace(url="redis://localhost:6379/0"),
    )
    monkeypatch.setattr(m, "_database_revisions", AsyncMock(return_value=("head", "head")))
    monkeypatch.setattr(m, "_schema_ready", AsyncMock(return_value=(True, [])))
    readiness = AsyncMock(return_value=(True, None))
    monkeypatch.setattr(m, "_demo_state", readiness)
    monkeypatch.setattr(m.redis_async, "from_url", lambda _: client)
    assert await m.run_preflight(discovery_mode="public-id") is True
    result = capsys.readouterr().out
    readiness.assert_awaited_once_with(
        "postgresql+asyncpg://localhost/demo", discovery_mode="public-id"
    )
    client.aclose.assert_awaited_once()
    assert "preflight=NON_NFC_PREREQUISITES_READY" in result
    assert "physical_nfc_gate=NOT_RUN_NO_NFC_HARDWARE" in result
    assert "patient_device_signature_gate=NOT_RUN" in result
    assert "clinician_browser_real_api_gate=NOT_RUN" in result
    assert "release_gate=HOLD" in result
    assert "nfc_uid=NOT_USED" in result
    assert "preflight=GO" not in result


@pytest.mark.asyncio
async def test_invalid_mode_fails_before_any_environment_or_data_access(monkeypatch):
    env = AsyncMock()
    monkeypatch.setattr(preflight, "require_demo_environment", env)
    with pytest.raises(ValueError, match="Unsupported patient discovery"):
        await preflight.run_preflight(discovery_mode="skip-all")
    env.assert_not_awaited()
