"""Strict HTTP adapter for the already-authoritative provider trust stack."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import (
    ProviderTrustRoutePrincipal,
    get_provider_trust_route_principal,
)
from app.models.provider import FacilityVerification, ProfessionalVerification
from app.services.provider_registration_service import (
    ProviderRegistrationError,
    normalize_professional_registration_authority_code,
    normalize_professional_registration_number,
)
from app.services.provider_trust_lifecycle import (
    AffiliationTransitionCommand,
    AffiliationTransitionFacts,
    FacilityTransitionCommand,
    FacilityTransitionFacts,
    ProfessionalTransitionCommand,
    ProfessionalTransitionFacts,
)
from app.services.provider_trust_lifecycle_application import (
    ProviderTrustLifecycleApplicationError,
    ProviderTrustLifecycleApplicationService,
    ProviderTrustLifecycleResult,
)


router = APIRouter(prefix="/api/v2/provider-trust", tags=["provider-trust"])


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _VersionRequest(_StrictModel):
    expected_version: int = Field(ge=1)


class ProfessionalSubmissionRequest(_VersionRequest):
    registration_authority_code: str
    registration_number: str


class ProfessionalEvidenceRequest(_VersionRequest):
    registration_authority_code: str
    registration_number_normalized: str
    verification_method: str
    verification_source: str
    verification_reference: str
    identity_binding_method: str
    identity_binding_status: str
    registration_valid_from: datetime | None = None
    registration_valid_until: datetime | None = None
    next_review_at: datetime | None = None


class DecisionRequest(_VersionRequest):
    decision_reason_code: str


class FacilityEvidenceRequest(_VersionRequest):
    verification_method: str
    verification_source: str
    verification_reference: str
    next_review_at: datetime | None = None


class AffiliationActivationRequest(_VersionRequest):
    valid_from: datetime | None = None
    valid_until: datetime | None = None


class ProviderTrustTransitionResponse(_StrictModel):
    resource_id: UUID
    lifecycle_type: str
    old_state: str
    new_state: str
    version: int
    idempotent_replay: bool


class ProviderTrustRouteError(Exception):
    """Stable public failure for a Phase-3E lifecycle decision."""

    def __init__(self, status_code: int, error_code: str) -> None:
        super().__init__(error_code)
        self.status_code = status_code
        self.error_code = error_code


async def provider_trust_route_error_response(
    _request: Request, exc: ProviderTrustRouteError
) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code, content={"error_code": exc.error_code}
    )


def _response(result: ProviderTrustLifecycleResult) -> ProviderTrustTransitionResponse:
    return ProviderTrustTransitionResponse(
        resource_id=result.resource_id,
        lifecycle_type=result.lifecycle_type,
        old_state=result.old_state,
        new_state=result.new_state,
        version=result.version,
        idempotent_replay=result.idempotent_replay,
    )


def _error(exc: ProviderTrustLifecycleApplicationError) -> ProviderTrustRouteError:
    mapping = {
        "INVALID_REQUEST": status.HTTP_400_BAD_REQUEST,
        "RESOURCE_NOT_FOUND": status.HTTP_404_NOT_FOUND,
        "AUTHORIZATION_DENIED": status.HTTP_403_FORBIDDEN,
        "LIFECYCLE_VERSION_CONFLICT": status.HTTP_409_CONFLICT,
        "LIFECYCLE_POLICY_DENIED": status.HTTP_409_CONFLICT,
        "IDEMPOTENCY_KEY_REUSED": status.HTTP_409_CONFLICT,
        "IDEMPOTENCY_IN_PROGRESS": status.HTTP_409_CONFLICT,
        "TRANSACTION_INTEGRITY_FAILURE": status.HTTP_503_SERVICE_UNAVAILABLE,
    }
    return ProviderTrustRouteError(
        mapping.get(exc.code, status.HTTP_503_SERVICE_UNAVAILABLE), exc.code
    )


async def _professional_resource_id(db: AsyncSession, provider_id: UUID) -> UUID:
    # Exit the read-only transaction before Phase 3E starts its owner
    # transaction; this avoids an implicit/autobegun transaction on ``db``.
    async with db.begin():
        resource_id = await db.scalar(
            select(ProfessionalVerification.id).where(
                ProfessionalVerification.provider_id == provider_id
            )
        )
    if resource_id is None:
        raise ProviderTrustRouteError(404, "RESOURCE_NOT_FOUND")
    return resource_id


async def _facility_resource_id(db: AsyncSession, facility_id: UUID) -> UUID:
    async with db.begin():
        resource_id = await db.scalar(
            select(FacilityVerification.id).where(
                FacilityVerification.facility_id == facility_id
            )
        )
    if resource_id is None:
        raise ProviderTrustRouteError(404, "RESOURCE_NOT_FOUND")
    return resource_id


async def _professional(
    *,
    principal: ProviderTrustRoutePrincipal,
    db: AsyncSession,
    resource_id: UUID,
    command: ProfessionalTransitionCommand,
    facts: ProfessionalTransitionFacts,
    expected_version: int,
    idempotency_key: str,
    route_recheck_no_grace: bool = False,
) -> ProviderTrustTransitionResponse:
    try:
        result = await ProviderTrustLifecycleApplicationService(db).apply_professional(
            actor_id=principal.actor_provider_id,
            authentication=principal.authentication,
            resource_id=resource_id,
            command=command,
            facts=facts,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            route_recheck_no_grace=route_recheck_no_grace,
        )
    except ProviderTrustLifecycleApplicationError as exc:
        raise _error(exc) from None
    return _response(result)


async def _facility(
    *,
    principal: ProviderTrustRoutePrincipal,
    db: AsyncSession,
    resource_id: UUID,
    command: FacilityTransitionCommand,
    facts: FacilityTransitionFacts,
    expected_version: int,
    idempotency_key: str,
) -> ProviderTrustTransitionResponse:
    try:
        result = await ProviderTrustLifecycleApplicationService(db).apply_facility(
            actor_id=principal.actor_provider_id,
            authentication=principal.authentication,
            resource_id=resource_id,
            command=command,
            facts=facts,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
        )
    except ProviderTrustLifecycleApplicationError as exc:
        raise _error(exc) from None
    return _response(result)


async def _affiliation(
    *,
    principal: ProviderTrustRoutePrincipal,
    db: AsyncSession,
    affiliation_id: UUID,
    command: AffiliationTransitionCommand,
    facts: AffiliationTransitionFacts,
    expected_version: int,
    idempotency_key: str,
) -> ProviderTrustTransitionResponse:
    try:
        result = await ProviderTrustLifecycleApplicationService(db).apply_affiliation(
            actor_id=principal.actor_provider_id,
            authentication=principal.authentication,
            resource_id=affiliation_id,
            command=command,
            facts=facts,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
        )
    except ProviderTrustLifecycleApplicationError as exc:
        raise _error(exc) from None
    return _response(result)


@router.post("/professional/me/submit", response_model=ProviderTrustTransitionResponse)
async def submit_professional(
    payload: ProfessionalSubmissionRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    principal: ProviderTrustRoutePrincipal = Depends(
        get_provider_trust_route_principal
    ),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        facts = ProfessionalTransitionFacts(
            registration_authority_code=normalize_professional_registration_authority_code(
                payload.registration_authority_code
            ),
            registration_number_normalized=normalize_professional_registration_number(
                payload.registration_number
            ),
        )
    except ProviderRegistrationError:
        raise ProviderTrustRouteError(400, "INVALID_REQUEST") from None
    return await _professional(
        principal=principal,
        db=db,
        resource_id=await _professional_resource_id(db, principal.actor_provider_id),
        command=ProfessionalTransitionCommand.SUBMIT,
        facts=facts,
        expected_version=payload.expected_version,
        idempotency_key=idempotency_key,
    )


async def _review_professional(
    provider_id: UUID,
    payload: _VersionRequest,
    command: ProfessionalTransitionCommand,
    facts: ProfessionalTransitionFacts,
    idempotency_key: str,
    principal: ProviderTrustRoutePrincipal,
    db: AsyncSession,
    *,
    recheck_no_grace: bool = False,
):
    return await _professional(
        principal=principal,
        db=db,
        resource_id=await _professional_resource_id(db, provider_id),
        command=command,
        facts=facts,
        expected_version=payload.expected_version,
        idempotency_key=idempotency_key,
        route_recheck_no_grace=recheck_no_grace,
    )


def _professional_evidence(
    payload: ProfessionalEvidenceRequest,
) -> ProfessionalTransitionFacts:
    try:
        return ProfessionalTransitionFacts(
            registration_authority_code=normalize_professional_registration_authority_code(
                payload.registration_authority_code
            ),
            registration_number_normalized=normalize_professional_registration_number(
                payload.registration_number_normalized
            ),
            verification_method=payload.verification_method,
            verification_source=payload.verification_source,
            verification_reference=payload.verification_reference,
            identity_binding_method=payload.identity_binding_method,
            identity_binding_status=payload.identity_binding_status,
            registration_valid_from=payload.registration_valid_from,
            registration_valid_until=payload.registration_valid_until,
            next_review_at=payload.next_review_at,
        )
    except ProviderRegistrationError:
        raise ProviderTrustRouteError(400, "INVALID_REQUEST") from None


@router.post(
    "/professional/{provider_id}/verify", response_model=ProviderTrustTransitionResponse
)
async def verify_professional(
    provider_id: UUID,
    payload: ProfessionalEvidenceRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    principal: ProviderTrustRoutePrincipal = Depends(
        get_provider_trust_route_principal
    ),
    db: AsyncSession = Depends(get_db_session),
):
    return await _review_professional(
        provider_id,
        payload,
        ProfessionalTransitionCommand.VERIFY,
        _professional_evidence(payload),
        idempotency_key,
        principal,
        db,
    )


@router.post(
    "/professional/{provider_id}/reject", response_model=ProviderTrustTransitionResponse
)
async def reject_professional(
    provider_id: UUID,
    payload: DecisionRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    principal: ProviderTrustRoutePrincipal = Depends(
        get_provider_trust_route_principal
    ),
    db: AsyncSession = Depends(get_db_session),
):
    return await _review_professional(
        provider_id,
        payload,
        ProfessionalTransitionCommand.REJECT,
        ProfessionalTransitionFacts(decision_reason_code=payload.decision_reason_code),
        idempotency_key,
        principal,
        db,
    )


@router.post(
    "/professional/{provider_id}/suspend",
    response_model=ProviderTrustTransitionResponse,
)
async def suspend_professional(
    provider_id: UUID,
    payload: DecisionRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    principal: ProviderTrustRoutePrincipal = Depends(
        get_provider_trust_route_principal
    ),
    db: AsyncSession = Depends(get_db_session),
):
    return await _review_professional(
        provider_id,
        payload,
        ProfessionalTransitionCommand.SUSPEND,
        ProfessionalTransitionFacts(decision_reason_code=payload.decision_reason_code),
        idempotency_key,
        principal,
        db,
    )


@router.post(
    "/professional/{provider_id}/restore",
    response_model=ProviderTrustTransitionResponse,
)
async def restore_professional(
    provider_id: UUID,
    payload: ProfessionalEvidenceRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    principal: ProviderTrustRoutePrincipal = Depends(
        get_provider_trust_route_principal
    ),
    db: AsyncSession = Depends(get_db_session),
):
    return await _review_professional(
        provider_id,
        payload,
        ProfessionalTransitionCommand.RESTORE,
        _professional_evidence(payload),
        idempotency_key,
        principal,
        db,
    )


@router.post(
    "/professional/{provider_id}/mark-recheck-due",
    response_model=ProviderTrustTransitionResponse,
)
async def mark_professional_recheck_due(
    provider_id: UUID,
    payload: _VersionRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    principal: ProviderTrustRoutePrincipal = Depends(
        get_provider_trust_route_principal
    ),
    db: AsyncSession = Depends(get_db_session),
):
    return await _review_professional(
        provider_id,
        payload,
        ProfessionalTransitionCommand.MARK_RECHECK_DUE,
        ProfessionalTransitionFacts(),
        idempotency_key,
        principal,
        db,
        recheck_no_grace=True,
    )


@router.post(
    "/professional/{provider_id}/complete-recheck",
    response_model=ProviderTrustTransitionResponse,
)
async def complete_professional_recheck(
    provider_id: UUID,
    payload: ProfessionalEvidenceRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    principal: ProviderTrustRoutePrincipal = Depends(
        get_provider_trust_route_principal
    ),
    db: AsyncSession = Depends(get_db_session),
):
    return await _review_professional(
        provider_id,
        payload,
        ProfessionalTransitionCommand.COMPLETE_RECHECK,
        _professional_evidence(payload),
        idempotency_key,
        principal,
        db,
    )


@router.post(
    "/professional/{provider_id}/mark-stale",
    response_model=ProviderTrustTransitionResponse,
)
async def mark_professional_stale(
    provider_id: UUID,
    payload: _VersionRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    principal: ProviderTrustRoutePrincipal = Depends(
        get_provider_trust_route_principal
    ),
    db: AsyncSession = Depends(get_db_session),
):
    return await _review_professional(
        provider_id,
        payload,
        ProfessionalTransitionCommand.MARK_STALE,
        ProfessionalTransitionFacts(),
        idempotency_key,
        principal,
        db,
    )


@router.post(
    "/professional/{provider_id}/revoke", response_model=ProviderTrustTransitionResponse
)
async def revoke_professional(
    provider_id: UUID,
    payload: DecisionRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    principal: ProviderTrustRoutePrincipal = Depends(
        get_provider_trust_route_principal
    ),
    db: AsyncSession = Depends(get_db_session),
):
    return await _review_professional(
        provider_id,
        payload,
        ProfessionalTransitionCommand.REVOKE,
        ProfessionalTransitionFacts(decision_reason_code=payload.decision_reason_code),
        idempotency_key,
        principal,
        db,
    )


@router.post(
    "/professional/{provider_id}/expire", response_model=ProviderTrustTransitionResponse
)
async def expire_professional(
    provider_id: UUID,
    payload: DecisionRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    principal: ProviderTrustRoutePrincipal = Depends(
        get_provider_trust_route_principal
    ),
    db: AsyncSession = Depends(get_db_session),
):
    return await _review_professional(
        provider_id,
        payload,
        ProfessionalTransitionCommand.EXPIRE,
        ProfessionalTransitionFacts(decision_reason_code=payload.decision_reason_code),
        idempotency_key,
        principal,
        db,
    )


async def _facility_route(
    facility_id: UUID,
    payload: _VersionRequest,
    command: FacilityTransitionCommand,
    facts: FacilityTransitionFacts,
    idempotency_key: str,
    principal: ProviderTrustRoutePrincipal,
    db: AsyncSession,
):
    return await _facility(
        principal=principal,
        db=db,
        resource_id=await _facility_resource_id(db, facility_id),
        command=command,
        facts=facts,
        expected_version=payload.expected_version,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/facilities/{facility_id}/submit", response_model=ProviderTrustTransitionResponse
)
async def submit_facility(
    facility_id: UUID,
    payload: _VersionRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    principal: ProviderTrustRoutePrincipal = Depends(
        get_provider_trust_route_principal
    ),
    db: AsyncSession = Depends(get_db_session),
):
    return await _facility_route(
        facility_id,
        payload,
        FacilityTransitionCommand.SUBMIT,
        FacilityTransitionFacts(),
        idempotency_key,
        principal,
        db,
    )


def _facility_evidence(payload: FacilityEvidenceRequest) -> FacilityTransitionFacts:
    return FacilityTransitionFacts(
        verification_method=payload.verification_method,
        verification_source=payload.verification_source,
        verification_reference=payload.verification_reference,
        next_review_at=payload.next_review_at,
    )


@router.post(
    "/facilities/{facility_id}/verify", response_model=ProviderTrustTransitionResponse
)
async def verify_facility(
    facility_id: UUID,
    payload: FacilityEvidenceRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    principal: ProviderTrustRoutePrincipal = Depends(
        get_provider_trust_route_principal
    ),
    db: AsyncSession = Depends(get_db_session),
):
    return await _facility_route(
        facility_id,
        payload,
        FacilityTransitionCommand.VERIFY,
        _facility_evidence(payload),
        idempotency_key,
        principal,
        db,
    )


@router.post(
    "/facilities/{facility_id}/reject", response_model=ProviderTrustTransitionResponse
)
async def reject_facility(
    facility_id: UUID,
    payload: DecisionRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    principal: ProviderTrustRoutePrincipal = Depends(
        get_provider_trust_route_principal
    ),
    db: AsyncSession = Depends(get_db_session),
):
    return await _facility_route(
        facility_id,
        payload,
        FacilityTransitionCommand.REJECT,
        FacilityTransitionFacts(decision_reason_code=payload.decision_reason_code),
        idempotency_key,
        principal,
        db,
    )


@router.post(
    "/facilities/{facility_id}/suspend", response_model=ProviderTrustTransitionResponse
)
async def suspend_facility(
    facility_id: UUID,
    payload: DecisionRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    principal: ProviderTrustRoutePrincipal = Depends(
        get_provider_trust_route_principal
    ),
    db: AsyncSession = Depends(get_db_session),
):
    return await _facility_route(
        facility_id,
        payload,
        FacilityTransitionCommand.SUSPEND,
        FacilityTransitionFacts(decision_reason_code=payload.decision_reason_code),
        idempotency_key,
        principal,
        db,
    )


@router.post(
    "/facilities/{facility_id}/restore", response_model=ProviderTrustTransitionResponse
)
async def restore_facility(
    facility_id: UUID,
    payload: FacilityEvidenceRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    principal: ProviderTrustRoutePrincipal = Depends(
        get_provider_trust_route_principal
    ),
    db: AsyncSession = Depends(get_db_session),
):
    return await _facility_route(
        facility_id,
        payload,
        FacilityTransitionCommand.RESTORE,
        _facility_evidence(payload),
        idempotency_key,
        principal,
        db,
    )


@router.post(
    "/facilities/{facility_id}/mark-recheck-required",
    response_model=ProviderTrustTransitionResponse,
)
async def mark_facility_recheck_required(
    facility_id: UUID,
    payload: _VersionRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    principal: ProviderTrustRoutePrincipal = Depends(
        get_provider_trust_route_principal
    ),
    db: AsyncSession = Depends(get_db_session),
):
    return await _facility_route(
        facility_id,
        payload,
        FacilityTransitionCommand.MARK_RECHECK_REQUIRED,
        FacilityTransitionFacts(),
        idempotency_key,
        principal,
        db,
    )


@router.post(
    "/facilities/{facility_id}/complete-recheck",
    response_model=ProviderTrustTransitionResponse,
)
async def complete_facility_recheck(
    facility_id: UUID,
    payload: FacilityEvidenceRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    principal: ProviderTrustRoutePrincipal = Depends(
        get_provider_trust_route_principal
    ),
    db: AsyncSession = Depends(get_db_session),
):
    return await _facility_route(
        facility_id,
        payload,
        FacilityTransitionCommand.COMPLETE_RECHECK,
        _facility_evidence(payload),
        idempotency_key,
        principal,
        db,
    )


@router.post(
    "/facilities/{facility_id}/close", response_model=ProviderTrustTransitionResponse
)
async def close_facility(
    facility_id: UUID,
    payload: DecisionRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    principal: ProviderTrustRoutePrincipal = Depends(
        get_provider_trust_route_principal
    ),
    db: AsyncSession = Depends(get_db_session),
):
    return await _facility_route(
        facility_id,
        payload,
        FacilityTransitionCommand.CLOSE,
        FacilityTransitionFacts(decision_reason_code=payload.decision_reason_code),
        idempotency_key,
        principal,
        db,
    )


async def _affiliation_route(
    affiliation_id: UUID,
    payload: _VersionRequest,
    command: AffiliationTransitionCommand,
    facts: AffiliationTransitionFacts,
    idempotency_key: str,
    principal: ProviderTrustRoutePrincipal,
    db: AsyncSession,
):
    return await _affiliation(
        principal=principal,
        db=db,
        affiliation_id=affiliation_id,
        command=command,
        facts=facts,
        expected_version=payload.expected_version,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/affiliations/{affiliation_id}/activate",
    response_model=ProviderTrustTransitionResponse,
)
async def activate_affiliation(
    affiliation_id: UUID,
    payload: AffiliationActivationRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    principal: ProviderTrustRoutePrincipal = Depends(
        get_provider_trust_route_principal
    ),
    db: AsyncSession = Depends(get_db_session),
):
    return await _affiliation_route(
        affiliation_id,
        payload,
        AffiliationTransitionCommand.ACTIVATE,
        AffiliationTransitionFacts(
            valid_from=payload.valid_from, valid_until=payload.valid_until
        ),
        idempotency_key,
        principal,
        db,
    )


def _affiliation_reason(payload: DecisionRequest) -> AffiliationTransitionFacts:
    return AffiliationTransitionFacts(decision_reason_code=payload.decision_reason_code)


@router.post(
    "/affiliations/{affiliation_id}/suspend",
    response_model=ProviderTrustTransitionResponse,
)
async def suspend_affiliation(
    affiliation_id: UUID,
    payload: DecisionRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    principal: ProviderTrustRoutePrincipal = Depends(
        get_provider_trust_route_principal
    ),
    db: AsyncSession = Depends(get_db_session),
):
    return await _affiliation_route(
        affiliation_id,
        payload,
        AffiliationTransitionCommand.SUSPEND,
        _affiliation_reason(payload),
        idempotency_key,
        principal,
        db,
    )


@router.post(
    "/affiliations/{affiliation_id}/restore",
    response_model=ProviderTrustTransitionResponse,
)
async def restore_affiliation(
    affiliation_id: UUID,
    payload: _VersionRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    principal: ProviderTrustRoutePrincipal = Depends(
        get_provider_trust_route_principal
    ),
    db: AsyncSession = Depends(get_db_session),
):
    return await _affiliation_route(
        affiliation_id,
        payload,
        AffiliationTransitionCommand.RESTORE,
        AffiliationTransitionFacts(),
        idempotency_key,
        principal,
        db,
    )


@router.post(
    "/affiliations/{affiliation_id}/revoke",
    response_model=ProviderTrustTransitionResponse,
)
async def revoke_affiliation(
    affiliation_id: UUID,
    payload: DecisionRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    principal: ProviderTrustRoutePrincipal = Depends(
        get_provider_trust_route_principal
    ),
    db: AsyncSession = Depends(get_db_session),
):
    return await _affiliation_route(
        affiliation_id,
        payload,
        AffiliationTransitionCommand.REVOKE,
        _affiliation_reason(payload),
        idempotency_key,
        principal,
        db,
    )


@router.post(
    "/affiliations/{affiliation_id}/expire",
    response_model=ProviderTrustTransitionResponse,
)
async def expire_affiliation(
    affiliation_id: UUID,
    payload: DecisionRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    principal: ProviderTrustRoutePrincipal = Depends(
        get_provider_trust_route_principal
    ),
    db: AsyncSession = Depends(get_db_session),
):
    return await _affiliation_route(
        affiliation_id,
        payload,
        AffiliationTransitionCommand.EXPIRE,
        _affiliation_reason(payload),
        idempotency_key,
        principal,
        db,
    )


@router.post(
    "/affiliations/{affiliation_id}/leave",
    response_model=ProviderTrustTransitionResponse,
)
async def leave_affiliation(
    affiliation_id: UUID,
    payload: DecisionRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    principal: ProviderTrustRoutePrincipal = Depends(
        get_provider_trust_route_principal
    ),
    db: AsyncSession = Depends(get_db_session),
):
    return await _affiliation_route(
        affiliation_id,
        payload,
        AffiliationTransitionCommand.LEAVE,
        _affiliation_reason(payload),
        idempotency_key,
        principal,
        db,
    )
