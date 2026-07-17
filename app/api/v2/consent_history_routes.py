from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import get_provider_context, get_scoped_session
from app.models.consent_grant import ConsentGrantLog
from app.models.provider_context import ProviderContext

router = APIRouter(prefix="/api/v2/consent", tags=["consent-history"])


class ConsentHistoryItem(BaseModel):
    id: str
    patient_id: str
    purpose: str
    status: str
    scope: list[str]
    issued_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    type: str


def _serialize_history(rows: list[ConsentGrantLog]) -> list[ConsentHistoryItem]:
    now = datetime.now(timezone.utc)
    result: list[ConsentHistoryItem] = []
    for row in rows:
        status_value = "revoked" if row.revoked_at else ("expired" if row.expires_at <= now else "active")
        result.append(ConsentHistoryItem(
            id=str(row.id), patient_id=row.patient_id, purpose=row.purpose,
            status=status_value, scope=list(row.scope), issued_at=row.issued_at,
            expires_at=row.expires_at, revoked_at=row.revoked_at,
            type="break-glass" if row.is_break_glass else "routine",
        ))
    return result


@router.get("/history/self", response_model=list[ConsentHistoryItem])
async def get_self_consent_history(
    patient_id: str = Depends(get_scoped_session),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        canonical_id = str(UUID(patient_id))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"error_code": "INVALID_PATIENT_ID"}) from exc
    rows = (await db.execute(
        select(ConsentGrantLog).where(ConsentGrantLog.patient_id == canonical_id)
        .order_by(ConsentGrantLog.issued_at.desc())
    )).scalars().all()
    return _serialize_history(rows)


@router.get("/history", response_model=list[ConsentHistoryItem])
async def get_consent_history(
    provider: ProviderContext = Depends(get_provider_context),
    db: AsyncSession = Depends(get_db_session),
):
    roles = set(provider.affiliation.roles or [])
    stmt = select(ConsentGrantLog).where(
        ConsentGrantLog.hospital_id == provider.hospital_id
    )
    if not roles.intersection({"admin", "privacy_officer", "auditor"}):
        stmt = stmt.where(ConsentGrantLog.clinician_id == provider.actor_uid)
    rows = (await db.execute(stmt.order_by(ConsentGrantLog.issued_at.desc()))).scalars().all()
    return _serialize_history(rows)
