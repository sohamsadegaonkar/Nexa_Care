"""Read-only emergency snapshot retrieval service.

The emergency snapshot is a projection table populated elsewhere. This module
never writes to that table; it only retrieves the current projection for a
masked patient identity after upstream authorization has succeeded.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from pydantic import JsonValue
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("nexa_logger")

_NO_KNOWN_MEDICAL_DATA_MESSAGE = "No Known Medical Data"


def _to_json_value(value: object) -> JsonValue:
    """Convert common DB scalar values into JSON-safe response values."""

    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, list):
        return [_to_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _to_json_value(item) for key, item in value.items()}
    return str(value)


def _serialize_snapshot_row(row: dict[str, object]) -> dict[str, JsonValue]:
    """Serialize a SQLAlchemy mapping without making clinical assumptions."""

    return {key: _to_json_value(value) for key, value in row.items()}


async def get_emergency_snapshot(patient_id: UUID, db_session: AsyncSession) -> dict[str, object]:
    """Return the read-only emergency snapshot for a masked patient UUID.

    The ``nexa_emergency_snapshot`` table is treated as a projection owned by a
    separate writer. This service intentionally performs no INSERT, UPDATE,
    DELETE, commit, or rollback operations. If no projection exists, it returns
    a structured ``No Known Medical Data`` response because the absence of known
    emergency facts is itself clinically relevant.
    """

    stmt = text(
        "SELECT * FROM nexa_emergency_snapshot "
        "WHERE patient_id = :patient_id LIMIT 1"
    ).bindparams(bindparam("patient_id", type_=PG_UUID(as_uuid=True)))

    try:
        result = await db_session.execute(stmt, {"patient_id": patient_id})
    except SQLAlchemyError as exc:
        logger.critical(json.dumps({
            "event": "emergency_snapshot_db_error",
            "patient_id": str(patient_id),
            "exception": str(exc),
            "action": "raising_503_fail_closed",
        }))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Emergency snapshot retrieval is temporarily unavailable.",
        ) from exc

    row = result.mappings().first()
    retrieved_at = datetime.now(timezone.utc)

    if row is None:
        return {
            "patient_id": patient_id,
            "snapshot_status": "no_known_medical_data",
            "message": _NO_KNOWN_MEDICAL_DATA_MESSAGE,
            "snapshot": {},
            "retrieved_at": retrieved_at,
        }

    return {
        "patient_id": patient_id,
        "snapshot_status": "available",
        "message": None,
        "snapshot": _serialize_snapshot_row(dict(row)),
        "retrieved_at": retrieved_at,
    }
