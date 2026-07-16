from __future__ import annotations

import pytest

from scripts.demo_environment import require_demo_environment


@pytest.mark.parametrize("environment", ["production", "prod", "staging", "preview", ""])
def test_demo_tools_refuse_unsafe_or_implicit_environments(monkeypatch, environment):
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    if environment:
        monkeypatch.setenv("ENV", environment)
    with pytest.raises(RuntimeError):
        require_demo_environment("test_tool")


@pytest.mark.parametrize("environment", ["alpha", "development", "test"])
def test_demo_tools_report_only_environment_and_database_host(monkeypatch, capsys, environment):
    monkeypatch.setenv("ENV", environment)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://private-user:private-password@db.example.test:5432/nexa",
    )
    assert require_demo_environment("test_tool") == environment
    output = capsys.readouterr().out
    assert f"target_environment={environment}" in output
    assert "database_host=db.example.test" in output
    assert "private-user" not in output
    assert "private-password" not in output
