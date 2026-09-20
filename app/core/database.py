"""Async SQLAlchemy engine and session factory for the provider layer."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from functools import lru_cache
import ssl
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import ConfigError, DatabaseConfig, get_database_config


def create_database_ssl_context(ca_path: Path | str | None) -> ssl.SSLContext | None:
    """Create a strictly verified SSLContext for Amazon RDS connections.

    Enforces CERT_REQUIRED and check_hostname = True using the authoritative CA bundle.
    Returns None if ca_path is None (used for local development and unit tests).
    """
    if ca_path is None:
        return None
    path = Path(ca_path).expanduser().resolve()
    if not path.is_file():
        raise ConfigError(f"DATABASE_SSL_CA_PATH not found: {path}")
    try:
        context = ssl.create_default_context(cafile=str(path))
    except (ssl.SSLError, OSError) as exc:
        raise ConfigError("DATABASE_SSL_CA_PATH contains invalid certificates") from exc
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def build_database_connect_args(
    config: DatabaseConfig | None = None,
) -> dict[str, Any]:
    """Build shared asyncpg connection arguments for API and migrations.

    Ensures application engine and Alembic migration runner share the identical
    caching and TLS verification contract.
    """
    if config is None:
        config = get_database_config()

    parsed = urlsplit(config.url)
    query_params = parse_qs(parsed.query)
    conflicting_keys = {"ssl", "sslmode", "sslrootcert", "sslcert", "sslkey"} & set(query_params.keys())
    if conflicting_keys:
        raise ConfigError(
            f"DATABASE_URL query contains conflicting TLS parameters: {', '.join(sorted(conflicting_keys))}. "
            "TLS configuration must be controlled via DATABASE_SSL_CA_PATH."
        )

    connect_args: dict[str, Any] = {
        "statement_cache_size": 0,
        "prepared_statement_cache_size": 0,
        "prepared_statement_name_func": lambda: f"__nexa_{uuid4().hex}__",
    }
    ssl_context = create_database_ssl_context(config.ssl_ca_path)
    if ssl_context is not None:
        connect_args["ssl"] = ssl_context

    return connect_args


@lru_cache
def get_async_engine() -> AsyncEngine:
    """Create a cached async engine bound to DATABASE_URL."""

    config = get_database_config()
    return create_async_engine(
        config.url,
        echo=config.echo_sql,
        pool_pre_ping=True,
        poolclass=NullPool,
        connect_args=build_database_connect_args(config),
    )


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return a cached session factory for dependency injection."""

    return async_sessionmaker(
        bind=get_async_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a request-scoped async database session."""

    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            yield session
        finally:
            await session.close()


async def get_provider_contact_mutation_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a distinct request-scoped async database session for contact mutations.

    Separates contact mutation transactions from authentication read transactions,
    ensuring provider_contact_assurance_service enters with a clean session and
    retains sole ownership of its transaction boundary.
    """

    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            yield session
        finally:
            await session.close()
