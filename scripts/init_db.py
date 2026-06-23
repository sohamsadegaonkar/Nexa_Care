#!/usr/bin/env python3
"""Initialize Nexa Care SQLAlchemy tables in the configured database.

This is a bootstrap utility for live/dev environments where the database has
not yet had the SQLAlchemy-managed schema created. It imports every ORM model
module before calling ``Base.metadata.create_all()``; otherwise SQLAlchemy would
not know those tables exist.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Explicit model imports are required for Base.metadata registration.
import app.models.document_review  # noqa: E402,F401
import app.models.nfc_card_registry  # noqa: E402,F401
import app.models.provider  # noqa: E402,F401
import app.models.shards  # noqa: E402,F401
from app.core.database import get_async_engine  # noqa: E402
from app.models.base import Base  # noqa: E402


async def create_tables() -> None:
    """Create all SQLAlchemy-managed tables if they do not already exist."""

    engine = get_async_engine()
    print("Initializing Nexa Care SQLAlchemy schema...")
    print(f"Tables registered: {', '.join(sorted(Base.metadata.tables.keys()))}")

    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        await conn.run_sync(Base.metadata.create_all)

    await engine.dispose()
    print("Nexa Care schema initialization complete.")


if __name__ == "__main__":
    asyncio.run(create_tables())
