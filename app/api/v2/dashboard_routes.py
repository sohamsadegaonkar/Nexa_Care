from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import get_provider_context
from app.models.consent_grant import ConsentGrantLog
from app.models.pipeline import ExtractionJob, ReviewQueueItem
from app.models.provider_context import ProviderContext

router = APIRouter(prefix="/api/v2/dashboard", tags=["dashboard"])


class DashboardMetrics(BaseModel):
    total_patients: int
    active_consents: int
    break_glass_grants: int
    review_backlog: int
    definitions_version: str = "2026-07-17"


@router.get("/metrics", response_model=DashboardMetrics)
async def get_dashboard_metrics(
    provider: ProviderContext = Depends(get_provider_context),
    db: AsyncSession = Depends(get_db_session),
):
    """Return database aggregates for the provider's active hospital only.

    total_patients is the number of distinct patients with a consent-ledger
    relationship to this hospital. active_consents counts unrevoked,
    unexpired ledger grants. break_glass_grants counts emergency/break-glass
    grants. review_backlog counts pending review items for patients belonging
    to this hospital's consent population.
    """

    hospital_id = provider.hospital.hospital_id
    total = await db.scalar(
        select(func.count(distinct(ConsentGrantLog.patient_id))).where(
            ConsentGrantLog.hospital_id == hospital_id
        )
    )
    active = await db.scalar(
        select(func.count(ConsentGrantLog.id)).where(
            ConsentGrantLog.hospital_id == hospital_id,
            ConsentGrantLog.revoked_at.is_(None),
            ConsentGrantLog.expires_at > func.now(),
        )
    )
    break_glass = await db.scalar(
        select(func.count(ConsentGrantLog.id)).where(
            ConsentGrantLog.hospital_id == hospital_id,
            ConsentGrantLog.is_break_glass.is_(True),
        )
    )
    backlog = await db.scalar(
        select(func.count(ReviewQueueItem.id))
        .join(ExtractionJob, ExtractionJob.id == ReviewQueueItem.job_id)
        .where(
            ReviewQueueItem.status == "pending",
            ExtractionJob.tenant_id == provider.hospital.hospital_id,
        )
    )
    return DashboardMetrics(
        total_patients=int(total or 0),
        active_consents=int(active or 0),
        break_glass_grants=int(break_glass or 0),
        review_backlog=int(backlog or 0),
    )
