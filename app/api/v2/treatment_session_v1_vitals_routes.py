"""Isolated Treatment Session V1 WRITE_VITALS HTTP boundary.

This router is intentionally not mounted in app.main until the Task-1
shared-route integration gate lands. The endpoint is nevertheless complete
and directly testable as an isolated APIRouter surface.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Request,
    Response,
    status,
)
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clinical_session_gate import (
    TreatmentSessionV1Authority,
    TreatmentSessionV1GateDenied,
    TreatmentSessionV1GateUnavailable,
    require_clinical_session,
)
from app.core.database import get_db_session
from app.core.dependencies import (
    enforce_current_clinical_capability,
    require_clinical_capability,
)
from app.models.provider_context import ProviderContext
from app.security.audit_context import AuditContext, AuditDomain
from app.security.clinical_access_policy import ClinicalAccessOperation
from app.security.provider_capabilities import ClinicalCapability
from app.services.treatment_session_v1_mint import (
    provider_session_binding_matches,
)
from app.services.treatment_vitals import (
    TreatmentVitalIdempotencyConflict,
    TreatmentVitalObservation,
    TreatmentVitalType,
    TreatmentVitalUnavailable,
    TreatmentVitalValidationError,
    TreatmentVitalWriteResult,
    blood_pressure_observation,
    heart_rate_observation,
    spo2_observation,
    stage_treatment_vital_write,
    temperature_observation,
    validate_treatment_vitals_idempotency_key,
)

router = APIRouter(
    prefix="/api/v2/treatment-session/v1",
    tags=["treatment-session-v1"],
)

_ENTRY_PROVIDER_DEPENDENCY = require_clinical_capability(
    ClinicalCapability.RECORD_READ
)
_WRITE_VITALS_AUTHORITY_DEPENDENCY = require_clinical_session(
    ClinicalAccessOperation.WRITE_VITALS
)


class _StrictVitalRequest(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        allow_inf_nan=False,
    )

    recorded_at: datetime


class BloodPressureVitalRequest(_StrictVitalRequest):
    kind: Literal["blood_pressure"]
    systolic_bp: int = Field(ge=1, le=999)
    diastolic_bp: int = Field(ge=1, le=999)


class HeartRateVitalRequest(_StrictVitalRequest):
    kind: Literal["heart_rate"]
    beats_per_minute: int = Field(ge=1, le=999)


class TemperatureVitalRequest(_StrictVitalRequest):
    kind: Literal["temperature"]
    celsius: int | float


class SpO2VitalRequest(_StrictVitalRequest):
    kind: Literal["spo2"]
    percentage: int | float = Field(ge=0, le=100)


TreatmentVitalRequest = Annotated[
    BloodPressureVitalRequest
    | HeartRateVitalRequest
    | TemperatureVitalRequest
    | SpO2VitalRequest,
    Field(discriminator="kind"),
]
_REQUEST_ADAPTER = TypeAdapter(TreatmentVitalRequest)


class TreatmentVitalWriteResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["committed"] = "committed"
    record_id: str
    encounter_id: str
    vital_type: TreatmentVitalType
    recorded_at: datetime
    idempotent_replay: bool


def _request_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, TreatmentVitalIdempotencyConflict):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": exc.code},
        )
    if isinstance(exc, TreatmentVitalValidationError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error_code": exc.code},
        )
    if isinstance(exc, TreatmentSessionV1GateDenied):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": exc.code},
        )
    if isinstance(
        exc,
        (TreatmentSessionV1GateUnavailable, TreatmentVitalUnavailable),
    ):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": exc.code},
        )
    raise TypeError("unsupported treatment-vitals error")


def _parse_request(raw_body: bytes) -> TreatmentVitalRequest:
    try:
        return _REQUEST_ADAPTER.validate_json(raw_body, strict=True)
    except (ValidationError, ValueError) as exc:
        raise TreatmentVitalValidationError(
            "TREATMENT_VITAL_REQUEST_INVALID"
        ) from exc


def _canonical_recorded_at(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise TreatmentVitalValidationError(
            "TREATMENT_VITAL_RECORDED_AT_INVALID"
        )
    return value.astimezone(timezone.utc)


def _observation_from_request(
    payload: TreatmentVitalRequest,
) -> TreatmentVitalObservation:
    recorded_at = _canonical_recorded_at(payload.recorded_at)
    if isinstance(payload, BloodPressureVitalRequest):
        return blood_pressure_observation(
            systolic_bp=payload.systolic_bp,
            diastolic_bp=payload.diastolic_bp,
            recorded_at=recorded_at,
        )
    if isinstance(payload, HeartRateVitalRequest):
        return heart_rate_observation(
            beats_per_minute=payload.beats_per_minute,
            recorded_at=recorded_at,
        )
    if isinstance(payload, TemperatureVitalRequest):
        return temperature_observation(
            celsius=payload.celsius,
            recorded_at=recorded_at,
        )
    if isinstance(payload, SpO2VitalRequest):
        return spo2_observation(
            percentage=payload.percentage,
            recorded_at=recorded_at,
        )
    raise TreatmentVitalValidationError("TREATMENT_VITAL_REQUEST_INVALID")


def _final_provider_matches_authority(
    *,
    provider: ProviderContext,
    authority: TreatmentSessionV1Authority,
) -> bool:
    return (
        provider.actor_uid == str(authority.provider_id)
        and provider.hospital_id == authority.hospital_id
        and provider_session_binding_matches(
            stored_hash=authority.provider_session_binding_hash,
            live_binding=provider.session_binding or "",
        )
    )


@router.post(
    "/vitals",
    status_code=status.HTTP_200_OK,
    response_model=TreatmentVitalWriteResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": _REQUEST_ADAPTER.json_schema(),
                }
            },
        }
    },
)
async def write_treatment_vital(
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
    ),
    provider: ProviderContext = Depends(_ENTRY_PROVIDER_DEPENDENCY),
    authority: TreatmentSessionV1Authority = Depends(
        _WRITE_VITALS_AUTHORITY_DEPENDENCY
    ),
    db: AsyncSession = Depends(get_db_session),
) -> TreatmentVitalWriteResponse:
    """Persist one exact WRITE_VITALS observation under Treatment Session V1."""

    response.headers["Cache-Control"] = "no-store"

    try:
        key = validate_treatment_vitals_idempotency_key(
            idempotency_key or ""
        )
        payload = _parse_request(await request.body())
        observation = _observation_from_request(payload)

        result: TreatmentVitalWriteResult = await stage_treatment_vital_write(
            db=db,
            authority=authority,
            observation=observation,
            idempotency_key=key,
            audit_context=AuditContext.for_hospital(
                hospital_id=str(authority.hospital_id),
                domain=AuditDomain.PATIENT_RECORD,
            ),
        )

        final_provider = await enforce_current_clinical_capability(
            request=request,
            provider=provider,
            db=db,
            capability=ClinicalCapability.RECORD_READ,
        )
        if not _final_provider_matches_authority(
            provider=final_provider,
            authority=authority,
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error_code": "TREATMENT_PROVIDER_SESSION_MISMATCH"
                },
            )
    except HTTPException:
        await db.rollback()
        raise
    except (
        TreatmentSessionV1GateDenied,
        TreatmentSessionV1GateUnavailable,
        TreatmentVitalIdempotencyConflict,
        TreatmentVitalUnavailable,
        TreatmentVitalValidationError,
    ) as exc:
        await db.rollback()
        raise _request_http_error(exc) from exc
    except Exception as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "TREATMENT_VITAL_WRITE_UNAVAILABLE"},
        ) from exc

    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "TREATMENT_VITAL_COMMIT_UNAVAILABLE"},
        ) from exc

    return TreatmentVitalWriteResponse(
        record_id=str(result.record_id),
        encounter_id=str(result.encounter_id),
        vital_type=result.vital_type,
        recorded_at=result.recorded_at,
        idempotent_replay=result.idempotent_replay,
    )
