"""Strict HTTP command adapter for provider-trust permission administration.

Exposes exactly two command routes under /api/v2/provider-trust:
- POST /api/v2/provider-trust/permissions/grant
- POST /api/v2/provider-trust/permissions/{grant_id}/revoke

Operates strictly via ProviderTrustRoutePrincipal and delegates all transaction
and authority logic to ProviderTrustPermissionApplicationService.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import (
    ProviderTrustRoutePrincipal,
    get_provider_trust_route_principal,
)
from app.security.trust_management_permissions import TrustManagementPermission
from app.services.provider_trust_permission_application import (
    ProviderTrustPermissionApplicationError,
    ProviderTrustPermissionApplicationService,
)
from app.services.provider_trust_permission_policy import RevocationReasonCode

router = APIRouter(prefix="/api/v2/provider-trust", tags=["provider-trust-permissions"])


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClientRevocationReasonCode(str, Enum):
    """Client-permitted revocation reasons.

    EXPIRED_SUPERSEDED is server-owned for internal expired-slot supersession
    and is deliberately excluded from client choice.
    """

    ACCESS_REMOVED = "ACCESS_REMOVED"
    ROLE_CHANGED = "ROLE_CHANGED"
    SECURITY_RESPONSE = "SECURITY_RESPONSE"
    GOVERNANCE_CHANGE = "GOVERNANCE_CHANGE"


class GrantPermissionRequest(_StrictModel):
    target_provider_id: UUID
    permission: TrustManagementPermission
    facility_id: UUID | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    governance_reference: str | None = None


class RevokePermissionRequest(_StrictModel):
    revocation_reason_code: ClientRevocationReasonCode
    governance_reference: str | None = None


class ProviderTrustPermissionResponse(_StrictModel):
    command: str
    grant_id: UUID
    target_provider_id: UUID
    permission: str
    scope_type: str
    facility_id: UUID | None = None
    superseded_grant_id: UUID | None = None
    idempotent_replay: bool


class ProviderTrustPermissionRouteError(Exception):
    """Stable public failure for permission administration routes."""

    def __init__(self, status_code: int, error_code: str) -> None:
        super().__init__(error_code)
        self.status_code = status_code
        self.error_code = error_code


async def provider_trust_permission_route_error_response(
    _request: Request, exc: ProviderTrustPermissionRouteError
) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code, content={"error_code": exc.error_code}
    )


def _map_application_error(
    exc: ProviderTrustPermissionApplicationError,
) -> ProviderTrustPermissionRouteError:
    """Map Phase-4C application and policy errors to safe public HTTP responses."""
    if exc.code == "TRUST_PERMISSION_POLICY_DENIED":
        policy_code = exc.policy_code or ""
        # Target-state collapse: prevent enumeration of target account/credential states
        if policy_code in {
            "TARGET_PROVIDER_NOT_FOUND",
            "TARGET_PROVIDER_INACTIVE",
            "TARGET_CREDENTIAL_INACTIVE",
        }:
            return ProviderTrustPermissionRouteError(
                status.HTTP_404_NOT_FOUND, "TARGET_PROVIDER_UNAVAILABLE"
            )
        if policy_code in {"ROOT_PERMISSION_OFFLINE_ONLY", "SELF_GRANT_PROHIBITED"}:
            return ProviderTrustPermissionRouteError(
                status.HTTP_403_FORBIDDEN, policy_code
            )
        if policy_code in {"ACTIVE_GRANT_EXISTS", "GRANT_ALREADY_REVOKED"}:
            return ProviderTrustPermissionRouteError(
                status.HTTP_409_CONFLICT, policy_code
            )
        if policy_code in {
            "GLOBAL_PERMISSION_FACILITY_PROHIBITED",
            "FACILITY_PERMISSION_FACILITY_REQUIRED",
            "INVALID_VALIDITY_INTERVAL",
            "INVALID_DATETIME_TIMEZONE",
            "INVALID_GOVERNANCE_REFERENCE",
            "INVALID_PERMISSION",
            "INVALID_REVOCATION_REASON",
        }:
            return ProviderTrustPermissionRouteError(
                status.HTTP_400_BAD_REQUEST, policy_code
            )
        if policy_code == "GRANT_STATE_INVALID":
            return ProviderTrustPermissionRouteError(
                status.HTTP_503_SERVICE_UNAVAILABLE, "TRANSACTION_INTEGRITY_FAILURE"
            )
        return ProviderTrustPermissionRouteError(
            status.HTTP_503_SERVICE_UNAVAILABLE, "TRANSACTION_INTEGRITY_FAILURE"
        )

    if exc.code == "MFA_STEP_UP_REQUIRED":
        return ProviderTrustPermissionRouteError(
            status.HTTP_428_PRECONDITION_REQUIRED, "MFA_STEP_UP_REQUIRED"
        )
    if exc.code == "AUTHORIZATION_DENIED":
        return ProviderTrustPermissionRouteError(
            status.HTTP_403_FORBIDDEN, "AUTHORIZATION_DENIED"
        )
    if exc.code == "RESOURCE_NOT_FOUND":
        return ProviderTrustPermissionRouteError(
            status.HTTP_404_NOT_FOUND, "RESOURCE_NOT_FOUND"
        )
    if exc.code == "INVALID_REQUEST":
        return ProviderTrustPermissionRouteError(
            status.HTTP_400_BAD_REQUEST, "INVALID_REQUEST"
        )
    if exc.code in {"IDEMPOTENCY_KEY_REUSED", "IDEMPOTENCY_IN_PROGRESS"}:
        return ProviderTrustPermissionRouteError(status.HTTP_409_CONFLICT, exc.code)
    if exc.code == "TRANSACTION_INTEGRITY_FAILURE":
        return ProviderTrustPermissionRouteError(
            status.HTTP_503_SERVICE_UNAVAILABLE, "TRANSACTION_INTEGRITY_FAILURE"
        )

    return ProviderTrustPermissionRouteError(
        status.HTTP_503_SERVICE_UNAVAILABLE, "TRANSACTION_INTEGRITY_FAILURE"
    )


@router.post(
    "/permissions/grant",
    response_model=ProviderTrustPermissionResponse,
    status_code=status.HTTP_200_OK,
)
async def grant_permission(
    payload: GrantPermissionRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    principal: ProviderTrustRoutePrincipal = Depends(
        get_provider_trust_route_principal
    ),
    db: AsyncSession = Depends(get_db_session),
) -> ProviderTrustPermissionResponse:
    """Grant an ordinary subordinate trust-management permission.

    Requires an active TRUST_PERMISSION_MANAGE grant with fresh MFA assurance.
    """
    if (
        payload.valid_from is not None
        and (
            payload.valid_from.tzinfo is None or payload.valid_from.utcoffset() is None
        )
    ) or (
        payload.valid_until is not None
        and (
            payload.valid_until.tzinfo is None
            or payload.valid_until.utcoffset() is None
        )
    ):
        raise ProviderTrustPermissionRouteError(
            status.HTTP_400_BAD_REQUEST, "INVALID_DATETIME_TIMEZONE"
        )

    try:
        svc = ProviderTrustPermissionApplicationService(db)
        result = await svc.apply_grant(
            actor_id=principal.actor_provider_id,
            authentication=principal.authentication,
            target_provider_id=payload.target_provider_id,
            permission=payload.permission,
            facility_id=payload.facility_id,
            valid_from=payload.valid_from,
            valid_until=payload.valid_until,
            governance_reference=payload.governance_reference,
            idempotency_key=idempotency_key,
        )
        return ProviderTrustPermissionResponse(
            command=result.command,
            grant_id=result.grant_id,
            target_provider_id=result.target_provider_id,
            permission=result.permission,
            scope_type=result.scope_type,
            facility_id=result.facility_id,
            superseded_grant_id=result.superseded_grant_id,
            idempotent_replay=result.idempotent_replay,
        )
    except ProviderTrustPermissionRouteError:
        raise
    except ProviderTrustPermissionApplicationError as exc:
        raise _map_application_error(exc) from exc
    except Exception as exc:
        raise ProviderTrustPermissionRouteError(
            status.HTTP_503_SERVICE_UNAVAILABLE, "TRANSACTION_INTEGRITY_FAILURE"
        ) from exc


@router.post(
    "/permissions/{grant_id}/revoke",
    response_model=ProviderTrustPermissionResponse,
    status_code=status.HTTP_200_OK,
)
async def revoke_permission(
    grant_id: UUID,
    payload: RevokePermissionRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    principal: ProviderTrustRoutePrincipal = Depends(
        get_provider_trust_route_principal
    ),
    db: AsyncSession = Depends(get_db_session),
) -> ProviderTrustPermissionResponse:
    """Revoke an active subordinate trust-management permission grant.

    Requires an active TRUST_PERMISSION_MANAGE grant with fresh MFA assurance.
    """
    try:
        svc = ProviderTrustPermissionApplicationService(db)
        result = await svc.apply_revoke(
            actor_id=principal.actor_provider_id,
            authentication=principal.authentication,
            grant_id=grant_id,
            revocation_reason_code=RevocationReasonCode(
                payload.revocation_reason_code.value
            ),
            governance_reference=payload.governance_reference,
            idempotency_key=idempotency_key,
        )
        return ProviderTrustPermissionResponse(
            command=result.command,
            grant_id=result.grant_id,
            target_provider_id=result.target_provider_id,
            permission=result.permission,
            scope_type=result.scope_type,
            facility_id=result.facility_id,
            superseded_grant_id=result.superseded_grant_id,
            idempotent_replay=result.idempotent_replay,
        )
    except ProviderTrustPermissionRouteError:
        raise
    except ProviderTrustPermissionApplicationError as exc:
        raise _map_application_error(exc) from exc
    except Exception as exc:
        raise ProviderTrustPermissionRouteError(
            status.HTTP_503_SERVICE_UNAVAILABLE, "TRANSACTION_INTEGRITY_FAILURE"
        ) from exc
