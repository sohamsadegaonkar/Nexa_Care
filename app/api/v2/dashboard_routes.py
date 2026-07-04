from fastapi import APIRouter, Depends
from pydantic import BaseModel
from app.core.dependencies import get_provider_context
from app.models.provider_context import ProviderContext

router = APIRouter(prefix="/api/v2/dashboard", tags=["dashboard"])


class DashboardMetrics(BaseModel):
    total_patients: int
    avg_appointment_duration: str
    revisit_rate: str
    productivity_score: int


@router.get("/metrics", response_model=DashboardMetrics)
async def get_dashboard_metrics(provider: ProviderContext = Depends(get_provider_context)):
    # In production this would aggregate from consent_ledger + patient tables
    return DashboardMetrics(
        total_patients=1247,
        avg_appointment_duration="18m 42s",
        revisit_rate="34%",
        productivity_score=87,
    )
