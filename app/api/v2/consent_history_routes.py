from fastapi import APIRouter, Depends
from typing import List
from pydantic import BaseModel
from datetime import datetime
from app.core.dependencies import get_provider_context
from app.models.provider_context import ProviderContext

router = APIRouter(prefix="/api/v2/consent", tags=["consent-history"])


class ConsentHistoryItem(BaseModel):
    id: str
    patient_id: str
    purpose: str
    issued_at: str
    expires_at: str
    type: str  # routine | break-glass


@router.get("/history", response_model=List[ConsentHistoryItem])
async def get_consent_history(provider: ProviderContext = Depends(get_provider_context)):
    # In production: query consent_ledger + consent_sessions
    return [
        ConsentHistoryItem(
            id="c1",
            patient_id="PAT-3921-XK9L",
            purpose="ROUTINE_CHECKUP",
            issued_at="2026-07-04 09:12",
            expires_at="2026-07-04 09:42",
            type="routine",
        ),
        ConsentHistoryItem(
            id="c2",
            patient_id="PAT-1044-PQ2M",
            purpose="EMERGENCY",
            issued_at="2026-07-03 14:55",
            expires_at="2026-07-03 15:10",
            type="break-glass",
        ),
    ]
