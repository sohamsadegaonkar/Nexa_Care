from fastapi import APIRouter, Depends
from pydantic import BaseModel
from app.core.dependencies import get_provider_context
from app.models.provider_context import ProviderContext

router = APIRouter(prefix="/api/v2/auth", tags=["roles"])

class RoleResponse(BaseModel):
    role: str
    provider_id: str

@router.get("/me/role", response_model=RoleResponse)
async def get_my_role(provider: ProviderContext = Depends(get_provider_context)):
    # In real system this would come from JWT claims or database
    # For demo we return a role based on provider_id suffix
    role = "clinician"
    if provider.provider_id.endswith("admin"):
        role = "admin"
    elif provider.provider_id.endswith("reception"):
        role = "receptionist"
    
    return RoleResponse(role=role, provider_id=provider.provider_id)