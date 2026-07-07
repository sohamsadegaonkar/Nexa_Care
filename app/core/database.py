"""Async SQLAlchemy engine and session factory for the provider layer."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from functools import lru_cache
from uuid import uuid4

from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_database_config


@lru_cache
def get_async_engine() -> AsyncEngine:
    """Create a cached async engine bound to DATABASE_URL."""

    config = get_database_config()
    return create_async_engine(
        config.url,
        echo=config.echo_sql,
        pool_pre_ping=True,
        poolclass=NullPool,
        connect_args={
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
            "prepared_statement_name_func": lambda: f"__nexa_{uuid4().hex}__",
        },
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
