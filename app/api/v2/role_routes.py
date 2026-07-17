from fastapi import APIRouter, Depends
from pydantic import BaseModel
from app.core.dependencies import get_provider_context
from app.models.provider_context import ProviderContext

router = APIRouter(prefix="/api/v2/auth", tags=["roles"])

class RoleResponse(BaseModel):
    role: str
    roles: list[str]
    provider_id: str

@router.get("/me/role", response_model=RoleResponse)
async def get_my_role(provider: ProviderContext = Depends(get_provider_context)):
    roles = sorted(set(provider.affiliation.roles or []))
    role_priority = ("admin", "privacy_officer", "auditor", "clinician", "receptionist")
    primary = next((candidate for candidate in role_priority if candidate in roles), roles[0] if roles else "none")
    return RoleResponse(role=primary, roles=roles, provider_id=provider.actor_uid)
