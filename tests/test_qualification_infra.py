"""Unit tests for tests/helpers/qualification_infra.py."""

from __future__ import annotations

import pytest

from tests.helpers.qualification_infra import (
    get_qualification_redis_url,
    normalize_async_postgres_url,
    normalize_sync_postgres_url,
    postgres_admin_url,
    postgres_database_url,
    require_disposable_database_name,
    require_loopback_postgres_url,
    require_loopback_redis_url,
)


def test_normalize_postgres_urls():
    sync_url = "postgresql://nexa:nexa_test@127.0.0.1:5432/nexa_qual_test"
    async_url = "postgresql+asyncpg://nexa:nexa_test@127.0.0.1:5432/nexa_qual_test"

    assert normalize_async_postgres_url(sync_url) == async_url
    assert normalize_async_postgres_url(async_url) == async_url

    assert normalize_sync_postgres_url(async_url) == sync_url
    assert normalize_sync_postgres_url(sync_url) == sync_url

    with pytest.raises(ValueError, match="Unrecognized PostgreSQL URL scheme"):
        normalize_async_postgres_url("mysql://localhost/db")


def test_require_loopback_postgres_url_accepts_loopback():
    require_loopback_postgres_url(
        "postgresql://nexa:pass@127.0.0.1:5432/nexa_qual_test"
    )
    require_loopback_postgres_url("postgresql+asyncpg://localhost:55439/nexa_qual_test")
    require_loopback_postgres_url("postgresql://LOCALHOST:5432/nexa_qual_test")


def test_require_loopback_postgres_url_rejects_remote():
    with pytest.raises(ValueError, match="must target a loopback host"):
        require_loopback_postgres_url(
            "postgresql://nexa:pass@db.internal:5432/nexa_qual_test"
        )

    with pytest.raises(ValueError, match="must target a loopback host"):
        require_loopback_postgres_url(
            "postgresql://nexa:pass@10.0.0.5:5432/nexa_qual_test"
        )

    with pytest.raises(ValueError, match="must target a loopback host"):
        require_loopback_postgres_url(
            "postgresql://nexa:pass@192.168.1.100:5432/nexa_qual_test"
        )


def test_require_disposable_database_name_accepts_valid():
    require_disposable_database_name("nexa_qual_worker")
    require_disposable_database_name("nexa_qual_ci_shared")
    require_disposable_database_name("nexa_qual_slice4_e2e_123")


def test_require_disposable_database_name_rejects_invalid():
    with pytest.raises(ValueError, match="starting with 'nexa_qual_'"):
        require_disposable_database_name("nexa_ci")

    with pytest.raises(ValueError, match="starting with 'nexa_qual_'"):
        require_disposable_database_name("postgres")

    with pytest.raises(ValueError, match="starting with 'nexa_qual_'"):
        require_disposable_database_name("nexa_production")

    with pytest.raises(ValueError, match="must be a non-empty string"):
        require_disposable_database_name("")

    with pytest.raises(ValueError, match="contains illegal characters"):
        require_disposable_database_name("nexa_qual_drop;--")


def test_postgres_admin_url_derivation_preserves_connection_details():
    base_5432 = "postgresql+asyncpg://ci_user:ci_pass@127.0.0.1:5432/nexa_qual_shared"
    admin_5432 = postgres_admin_url(base_5432)
    assert admin_5432 == "postgresql+asyncpg://ci_user:ci_pass@127.0.0.1:5432/postgres"

    base_55439 = "postgresql://local_dev:dev_pass@localhost:55439/nexa_qual_worker"
    admin_55439 = postgres_admin_url(base_55439)
    assert (
        admin_55439
        == "postgresql+asyncpg://local_dev:dev_pass@localhost:55439/postgres"
    )


def test_postgres_database_url_preserves_connection_details():
    base = "postgresql+asyncpg://nexa:nexa_test@127.0.0.1:5432/nexa_qual_source"
    db_url = postgres_database_url("nexa_qual_worker", base)
    assert (
        db_url == "postgresql+asyncpg://nexa:nexa_test@127.0.0.1:5432/nexa_qual_worker"
    )

    with pytest.raises(ValueError, match="starting with 'nexa_qual_'"):
        postgres_database_url("nexa_ci", base)


def test_require_loopback_redis_url():
    require_loopback_redis_url("redis://127.0.0.1:6379/0")
    require_loopback_redis_url("redis://localhost:6389/1")

    with pytest.raises(ValueError, match="must target a loopback host"):
        require_loopback_redis_url("redis://cache.internal:6379/0")

    with pytest.raises(ValueError, match="must target a loopback host"):
        require_loopback_redis_url("redis://10.0.0.8:6379/0")

    with pytest.raises(ValueError, match="Unrecognized Redis scheme"):
        require_loopback_redis_url("http://localhost:6379")


def test_get_qualification_redis_url(monkeypatch):
    monkeypatch.setenv("TEST_REDIS_URL", "redis://127.0.0.1:6380/2")
    assert get_qualification_redis_url() == "redis://127.0.0.1:6380/2"

    monkeypatch.delenv("TEST_REDIS_URL")
    monkeypatch.setenv("UPSTASH_REDIS_URL", "redis://localhost:6379/0")
    assert get_qualification_redis_url() == "redis://localhost:6379/0"
