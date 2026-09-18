"""Live provider-trust revalidation for Treatment Session V1 workflows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.security.clinical_policy import CLINICAL_CONTACT_ASSURANCE_POLICY
from app.security.provider_capabilities import ClinicalCapability
from app.services.clinical_eligibility import (
    ClinicalAuthenticationMethod,
    ClinicalEligibilityDenialCode,
    ClinicalEligibilityService,
    ClinicalEligibilityUnavailable,
    DelegatedInitiationAssurance,
)
from app.services.signed_treatment_session_v1 import (
    SIGNED_TREATMENT_SESSION_V1_PROTOCOL_VERSION,
)


class TreatmentSessionV1AuthorityUnavailable(RuntimeError):
    """Authoritative provider trust state could not be evaluated."""


@dataclass(frozen=True, slots=True)
class TreatmentSessionV1ProviderIneligible(RuntimeError):
    """The challenge-bound provider is no longer clinically eligible."""

    denial_code: ClinicalEligibilityDenialCode

    def __str__(self) -> str:
        return self.denial_code.value


def _aware_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("missing delegated assurance timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("delegated assurance timestamp must be timezone-aware")
    return parsed


async def assert_live_treatment_session_v1_provider(
    *, db: AsyncSession, request_data: dict
) -> None:
    """Reload current provider/facility/affiliation/capability trust.

    Treatment approval is invalid if the provider loses the capability or
    facility/affiliation trust that was required to initiate the workflow.
    Request-time assurance is therefore only evidence; it never overrides
    current PostgreSQL trust state.
    """

    if (
        request_data.get("protocol_version")
        != SIGNED_TREATMENT_SESSION_V1_PROTOCOL_VERSION
    ):
        raise TreatmentSessionV1ProviderIneligible(
            ClinicalEligibilityDenialCode.DELEGATED_WORKFLOW_BINDING_INVALID
        )

    try:
        provider_id = UUID(str(request_data["provider_id"]))
        hospital_id = UUID(str(request_data["hospital_id"]))
        request_id = UUID(str(request_data["request_id"]))
        authentication_method = ClinicalAuthenticationMethod(
            str(request_data["clinical_authentication_method"])
        )
        authorization = DelegatedInitiationAssurance(
            initiated_by_provider_id=provider_id,
            initiated_hospital_id=hospital_id,
            initiated_at=_aware_datetime(request_data["clinical_initiated_at"]),
            authentication_method=authentication_method,
            mfa_verified_at=_aware_datetime(request_data["clinical_mfa_verified_at"]),
            assurance_policy_version=str(
                request_data["clinical_assurance_policy_version"]
            ),
            workflow_id=request_id,
            consent_request_id=request_id,
            required_capability=ClinicalCapability.CONSENT_REQUEST,
            workflow_authorization_current=request_data.get("status")
            in {"pending", "approved"},
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise TreatmentSessionV1ProviderIneligible(
            ClinicalEligibilityDenialCode.DELEGATED_INITIATION_ASSURANCE_INVALID
        ) from exc

    try:
        result = await ClinicalEligibilityService(
            contact_assurance_policy=CLINICAL_CONTACT_ASSURANCE_POLICY
        ).evaluate_delegated(
            db,
            provider_id,
            hospital_id,
            authorization,
            ClinicalCapability.CONSENT_REQUEST,
        )
    except ClinicalEligibilityUnavailable as exc:
        raise TreatmentSessionV1AuthorityUnavailable(
            "clinical trust unavailable"
        ) from exc

    if not result.allowed:
        raise TreatmentSessionV1ProviderIneligible(
            result.denial_code
            or ClinicalEligibilityDenialCode.TRUST_STATE_INTEGRITY_FAILURE
        )
