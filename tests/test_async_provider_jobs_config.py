"""Configuration contract tests for the dormant Scenario 6 A1 settings."""

from __future__ import annotations

import pytest

from app.core.config import ConfigError, get_document_extraction_config


def _base_environment(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DOCUMENT_EXTRACTION_PROVIDER", "aws_textract")
    monkeypatch.setenv("DOCUMENT_AI_PROVIDER_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("DOCUMENT_AI_JOB_MAX_ATTEMPTS", "3")
    monkeypatch.delenv("DOCUMENT_AI_MAX_ATTEMPTS", raising=False)


def test_a1_reconciliation_defaults_and_dormant_async_flag(monkeypatch) -> None:
    _base_environment(monkeypatch)
    for name in (
        "DOCUMENT_AI_ASYNC_MULTIPAGE_ENABLED",
        "DOCUMENT_AI_RECONCILIATION_MAX_ATTEMPTS",
        "DOCUMENT_AI_RECONCILIATION_WINDOW_SECONDS",
        "DOCUMENT_AI_RECONCILIATION_BATCH_SIZE",
        "DOCUMENT_AI_RECONCILIATION_INTERVAL_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)

    config = get_document_extraction_config()

    assert config.async_multipage_enabled is False
    assert config.reconciliation_max_attempts == 3
    assert config.reconciliation_window_seconds == 900
    assert config.reconciliation_batch_size == 25
    assert config.reconciliation_interval_seconds == 2


@pytest.mark.parametrize(
    ("name", "low", "high"),
    [
        ("DOCUMENT_AI_RECONCILIATION_MAX_ATTEMPTS", 1, 5),
        ("DOCUMENT_AI_RECONCILIATION_WINDOW_SECONDS", 60, 86400),
        ("DOCUMENT_AI_RECONCILIATION_BATCH_SIZE", 1, 100),
        ("DOCUMENT_AI_RECONCILIATION_INTERVAL_SECONDS", 1, 60),
    ],
)
def test_a1_reconciliation_bounds_accept_edges(monkeypatch, name, low, high) -> None:
    _base_environment(monkeypatch)
    monkeypatch.setenv(name, str(low))
    assert get_document_extraction_config()
    monkeypatch.setenv(name, str(high))
    assert get_document_extraction_config()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("DOCUMENT_AI_RECONCILIATION_MAX_ATTEMPTS", "0"),
        ("DOCUMENT_AI_RECONCILIATION_MAX_ATTEMPTS", "6"),
        ("DOCUMENT_AI_RECONCILIATION_WINDOW_SECONDS", "59"),
        ("DOCUMENT_AI_RECONCILIATION_WINDOW_SECONDS", "86401"),
        ("DOCUMENT_AI_RECONCILIATION_BATCH_SIZE", "0"),
        ("DOCUMENT_AI_RECONCILIATION_BATCH_SIZE", "101"),
        ("DOCUMENT_AI_RECONCILIATION_INTERVAL_SECONDS", "0"),
        ("DOCUMENT_AI_RECONCILIATION_INTERVAL_SECONDS", "61"),
    ],
)
def test_a1_reconciliation_bounds_fail_closed(monkeypatch, name, value) -> None:
    _base_environment(monkeypatch)
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError):
        get_document_extraction_config()


def test_async_flag_parses_but_does_not_change_provider_selection(monkeypatch) -> None:
    _base_environment(monkeypatch)
    monkeypatch.setenv("DOCUMENT_AI_ASYNC_MULTIPAGE_ENABLED", "true")
    config = get_document_extraction_config()
    assert config.async_multipage_enabled is True
    assert config.provider == "aws_textract"


def test_async_flag_rejects_ambiguous_values(monkeypatch) -> None:
    _base_environment(monkeypatch)
    monkeypatch.setenv("DOCUMENT_AI_ASYNC_MULTIPAGE_ENABLED", "maybe")
    with pytest.raises(ConfigError):
        get_document_extraction_config()
