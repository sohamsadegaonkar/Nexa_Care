"""Dry-run inventory for staged removal of legacy plaintext patient columns.

Outputs counts only.  It never selects or prints a PII value.
"""

from __future__ import annotations

import asyncio
import json

from sqlalchemy import text

from app.core.database import get_async_engine

_COLUMNS = (
    "full_name", "date_of_birth", "gender", "phone", "email", "abha_id",
    "address_line1", "address_line2", "city", "state", "pincode",
    "emergency_contact_name", "emergency_contact_phone",
)


async def inspect() -> dict[str, object]:
    aggregates = ", ".join(
        f"COUNT(*) FILTER (WHERE {column} IS NOT NULL) AS {column}_non_null"
        for column in _COLUMNS
    )
    async with get_async_engine().connect() as connection:
        result = await connection.execute(text(f"SELECT COUNT(*) AS total_rows, {aggregates} FROM public.patients"))
        counts = dict(result.mappings().one())
    return {
        "mode": "dry_run",
        "table": "public.patients",
        "contains_values": False,
        "counts": {key: int(value or 0) for key, value in counts.items()},
        "next_step": "backup_then_migrate_or_quarantine_before_validating_or_dropping_columns",
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(inspect()), sort_keys=True))
