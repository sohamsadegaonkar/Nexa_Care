"""Transactional application boundary for prescribing-eligibility decisions."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.provider import (
    PrescribingEligibilityDecision,
    PrescribingEligibilityReasonCode,
    PrescribingEligibilitySourceType,
    PrescribingEligibilityStatus,
    PrescribingPractitionerClass,
    PrescribingRestrictionCode,
    ProfessionalVerification,
    ProfessionalVerificationStatus,
    ProviderCredential,
    ProviderIdentity,
    ProviderTrustPermissionGrant,
)
from app.observability.provider_trust_events import ProviderTrustAuditEvent
from app.security.audit_context import AuditContext, AuditDomain
from app.security.trust_management_permissions import (
    TrustManagementPermission,
    TrustPermissionScope,
)
from app.services.audit_outbox import enqueue_audit_event
from app.services.policy_service import validate_idempotency_key
from app.services.prescribing_eligibility_policy import (
    PRESCRIBER_ELIGIBILITY_POLICY_VERSION,
)
from app.services.provider_trust_authorization import (
    ProviderTrustAuthorizationService,
    TrustManagementAuthentication,
)

_IDEMPOTENCY_TENANT = "platform-prescribing-eligibility"
_OPERATION = "provider.prescribing_eligibility.review.v1"
_MAX_HUMAN_ATTESTATION_VALIDITY = timedelta(days=30)
_MFA_FRESHNESS_WINDOW = timedelta(minutes=15)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_ALLOWED_POSITIVE_SOURCES = frozenset(
    {
        PrescribingEligibilitySourceType.NMR,
        PrescribingEligibilitySourceType.SMR,
        PrescribingEligibilitySourceType.COMPETENT_MEDICAL_COUNCIL,
    }
)
_ALLOWED_POSITIVE_REASONS = frozenset(
    {
        PrescribingEligibilityReasonCode.PRIMARY_SOURCE_CURRENT_FULL_RMP,
        PrescribingEligibilityReasonCode.RECHECK_CONFIRMED_CURRENT,
    }
)

_IDEMPOTENCY_SELECT = text("""
    SELECT request_hash, response_status, response_payload
    FROM public.mutation_idempotency
    WHERE tenant_id = :tenant_id AND operation = :operation AND idempotency_key = :key
""")
_IDEMPOTENCY_RESERVE = text("""
    INSERT INTO public.mutation_idempotency
      (tenant_id, actor_id, operation, resource_id, idempotency_key, request_hash,
       created_at, retention_expires_at)
    VALUES
      (:tenant_id, :actor_id, :operation, :resource_id, :key, :request_hash,
       now(), now() + interval '90 days')
    ON CONFLICT (tenant_id, operation, idempotency_key) DO NOTHING
    RETURNING id
""")
_IDEMPOTENCY_COMPLETE = text("""
    UPDATE public.mutation_idempotency
    SET response_status = 200,
        response_payload = CAST(:payload AS JSONB),
        resulting_resource_version = :version
    WHERE tenant_id = :tenant_id AND operation = :operation AND idempotency_key = :key
""")


class PrescribingEligibilityApplicationError(RuntimeError):
    """Stable failure for the prescribing-eligibility review boundary."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class PrescribingEligibilityApplicationResult:
    decision_id: UUID
    provider_id: UUID
    professional_verification_id: UUID
    version: int
    status: str
    practitioner_class: str
    source_type: str
    valid_until: datetime
    idempotent_replay: bool


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PrescribingEligibilityApplicationError("INVALID_REQUEST")
    return value.astimezone(timezone.utc)


def _enum(value: object, enum_type: type[Enum]) -> Enum:
    try:
        return value if isinstance(value, enum_type) else enum_type(str(value))
    except (TypeError, ValueError) as exc:
        raise PrescribingEligibilityApplicationError("INVALID_REQUEST") from exc


def _clean_source_reference(value: object) -> str:
    if not isinstance(value, str):
        raise PrescribingEligibilityApplicationError("INVALID_REQUEST")
    cleaned = value.strip()
    if not cleaned or len(cleaned) > 255:
        raise PrescribingEligibilityApplicationError("INVALID_REQUEST")
    return cleaned


def _clean_evidence_sha256(value: object) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise PrescribingEligibilityApplicationError("INVALID_REQUEST")
    return value


def _validate_decision_shape(
    *,
    status: PrescribingEligibilityStatus,
    practitioner_class: PrescribingPractitionerClass,
    source_type: PrescribingEligibilitySourceType,
    reason: PrescribingEligibilityReasonCode,
    restriction: PrescribingRestrictionCode | None,
) -> None:
    if status is PrescribingEligibilityStatus.ELIGIBLE:
        if (
            practitioner_class
            is not PrescribingPractitionerClass.FULL_RMP_MODERN_MEDICINE
            or source_type not in _ALLOWED_POSITIVE_SOURCES
            or reason not in _ALLOWED_POSITIVE_REASONS
            or restriction is not None
        ):
            raise PrescribingEligibilityApplicationError("DECISION_POLICY_DENIED")
    if status is PrescribingEligibilityStatus.RESTRICTED and restriction is None:
        raise PrescribingEligibilityApplicationError("DECISION_POLICY_DENIED")
    if (
        status is PrescribingEligibilityStatus.SOURCE_UNAVAILABLE
        and reason is not PrescribingEligibilityReasonCode.SOURCE_UNAVAILABLE
    ):
        raise PrescribingEligibilityApplicationError("DECISION_POLICY_DENIED")
    if (
        status is PrescribingEligibilityStatus.SUSPENDED
        and reason is not PrescribingEligibilityReasonCode.PROFESSIONAL_SUSPENDED
    ):
        raise PrescribingEligibilityApplicationError("DECISION_POLICY_DENIED")
    if (
        status is PrescribingEligibilityStatus.REVOKED
        and reason is not PrescribingEligibilityReasonCode.PROFESSIONAL_REVOKED
    ):
        raise PrescribingEligibilityApplicationError("DECISION_POLICY_DENIED")
    if (
        status is PrescribingEligibilityStatus.EXPIRED
        and reason is not PrescribingEligibilityReasonCode.PROFESSIONAL_EXPIRED
    ):
        raise PrescribingEligibilityApplicationError("DECISION_POLICY_DENIED")
    if source_type is PrescribingEligibilitySourceType.HPR and status is PrescribingEligibilityStatus.ELIGIBLE:
        raise PrescribingEligibilityApplicationError("DECISION_POLICY_DENIED")


def _assert_positive_professional_state(
    professional: ProfessionalVerification,
    moment: datetime,
) -> None:
    try:
        state = ProfessionalVerificationStatus(professional.status)
    except (TypeError, ValueError) as exc:
        raise PrescribingEligibilityApplicationError(
            "PROFESSIONAL_VERIFICATION_NOT_CURRENT"
        ) from exc
    if state is not ProfessionalVerificationStatus.VERIFIED:
        raise PrescribingEligibilityApplicationError(
            "PROFESSIONAL_VERIFICATION_NOT_CURRENT"
        )
    if professional.verified_at is None:
        raise PrescribingEligibilityApplicationError(
            "PROFESSIONAL_VERIFICATION_NOT_CURRENT"
        )
    if _aware_utc(professional.verified_at) > moment:
        raise PrescribingEligibilityApplicationError(
            "PROFESSIONAL_VERIFICATION_NOT_CURRENT"
        )
    if professional.registration_valid_from is not None and _aware_utc(
        professional.registration_valid_from
    ) > moment:
        raise PrescribingEligibilityApplicationError(
            "PROFESSIONAL_VERIFICATION_NOT_CURRENT"
        )
    if professional.registration_valid_until is not None and _aware_utc(
        professional.registration_valid_until
    ) <= moment:
        raise PrescribingEligibilityApplicationError(
            "PROFESSIONAL_VERIFICATION_NOT_CURRENT"
        )
    if professional.next_review_at is not None and _aware_utc(
        professional.next_review_at
    ) <= moment:
        raise PrescribingEligibilityApplicationError(
            "PROFESSIONAL_VERIFICATION_NOT_CURRENT"
        )
    if professional.authoritative_adverse_signal_at is not None:
        raise PrescribingEligibilityApplicationError(
            "PROFESSIONAL_VERIFICATION_NOT_CURRENT"
        )
    if professional.identity_binding_status != "MATCHED":
        raise PrescribingEligibilityApplicationError(
            "PROFESSIONAL_IDENTITY_BINDING_REQUIRED"
        )


def _decision_valid_until(
    professional: ProfessionalVerification,
    *,
    status: PrescribingEligibilityStatus,
    moment: datetime,
) -> datetime:
    upper = moment + _MAX_HUMAN_ATTESTATION_VALIDITY
    if status is PrescribingEligibilityStatus.ELIGIBLE:
        for boundary in (
            professional.registration_valid_until,
            professional.next_review_at,
        ):
            if boundary is not None:
                bound = _aware_utc(boundary)
                if bound <= moment:
                    raise PrescribingEligibilityApplicationError(
                        "PROFESSIONAL_VERIFICATION_NOT_CURRENT"
                    )
                upper = min(upper, bound)
    return upper


def _request_hash(
    *,
    actor_id: UUID,
    target_provider_id: UUID,
    expected_professional_verification_version: int,
    expected_previous_decision_version: int,
    status: PrescribingEligibilityStatus,
    practitioner_class: PrescribingPractitionerClass,
    source_type: PrescribingEligibilitySourceType,
    source_reference: str,
    evidence_sha256: str,
    reason: PrescribingEligibilityReasonCode,
    restriction: PrescribingRestrictionCode | None,
) -> str:
    canonical = {
        "actor_id": str(actor_id),
        "evidence_sha256": evidence_sha256,
        "expected_previous_decision_version": expected_previous_decision_version,
        "expected_professional_verification_version": (
            expected_professional_verification_version
        ),
        "operation": _OPERATION,
        "policy_version": PRESCRIBER_ELIGIBILITY_POLICY_VERSION,
        "practitioner_class": practitioner_class.value,
        "reason": reason.value,
        "restriction": restriction.value if restriction is not None else None,
        "source_reference": source_reference,
        "source_type": source_type.value,
        "status": status.value,
        "target_provider_id": str(target_provider_id),
    }
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class PrescribingEligibilityApplicationService:
    """Create one immutable prescribing decision under current reviewer authority."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._authorization = ProviderTrustAuthorizationService()

    def _replay(
        self,
        row: Any,
        *,
        expected_hash: str,
    ) -> PrescribingEligibilityApplicationResult:
        if row.request_hash != expected_hash:
            raise PrescribingEligibilityApplicationError("IDEMPOTENCY_KEY_REUSED")
        if row.response_status != 200 or not row.response_payload:
            raise PrescribingEligibilityApplicationError("IDEMPOTENCY_IN_PROGRESS")
        try:
            payload = (
                row.response_payload
                if isinstance(row.response_payload, dict)
                else json.loads(row.response_payload)
            )
            return PrescribingEligibilityApplicationResult(
                decision_id=UUID(payload["decision_id"]),
                provider_id=UUID(payload["provider_id"]),
                professional_verification_id=UUID(
                    payload["professional_verification_id"]
                ),
                version=int(payload["version"]),
                status=str(payload["status"]),
                practitioner_class=str(payload["practitioner_class"]),
                source_type=str(payload["source_type"]),
                valid_until=datetime.fromisoformat(str(payload["valid_until"])),
                idempotent_replay=True,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PrescribingEligibilityApplicationError(
                "TRANSACTION_INTEGRITY_FAILURE"
            ) from exc

    async def _lock_provider_identities(
        self,
        provider_ids: set[UUID],
    ) -> dict[UUID, ProviderIdentity | None]:
        rows: dict[UUID, ProviderIdentity | None] = {}
        for provider_id in sorted(provider_ids):
            rows[provider_id] = (
                await self.db.execute(
                    select(ProviderIdentity)
                    .where(ProviderIdentity.id == provider_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
        return rows

    async def _lock_actor_credential_and_grants(
        self, actor_id: UUID
    ) -> tuple[ProviderCredential | None, list[ProviderTrustPermissionGrant]]:
        credential = (
            await self.db.execute(
                select(ProviderCredential)
                .where(ProviderCredential.provider_id == actor_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        grants = list(
            (
                await self.db.execute(
                    select(ProviderTrustPermissionGrant)
                    .where(ProviderTrustPermissionGrant.provider_id == actor_id)
                    .order_by(ProviderTrustPermissionGrant.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
        return credential, grants

    def _authorize_reviewer(
        self,
        *,
        actor: ProviderIdentity | None,
        actor_grants: list[ProviderTrustPermissionGrant],
        authentication: TrustManagementAuthentication,
        target_provider_id: UUID,
        moment: datetime,
    ) -> None:
        if actor is None or actor.id == target_provider_id:
            raise PrescribingEligibilityApplicationError("AUTHORIZATION_DENIED")
        denial = self._authorization._strong_auth(actor, authentication, moment)
        if denial is not None:
            raise PrescribingEligibilityApplicationError("AUTHORIZATION_DENIED")

        mfa_at = authentication.mfa_verified_at
        if (
            mfa_at is None
            or mfa_at.tzinfo is None
            or mfa_at.utcoffset() is None
            or mfa_at > moment
        ):
            raise PrescribingEligibilityApplicationError("AUTHORIZATION_DENIED")
        if moment - mfa_at > _MFA_FRESHNESS_WINDOW:
            raise PrescribingEligibilityApplicationError("MFA_STEP_UP_REQUIRED")

        decision = self._authorization._matching_grant(
            actor_grants,
            TrustManagementPermission.PRESCRIBING_ELIGIBILITY_REVIEW,
            None,
            moment,
        )
        if (
            not decision.allowed
            or decision.scope is not TrustPermissionScope.GLOBAL
        ):
            raise PrescribingEligibilityApplicationError("AUTHORIZATION_DENIED")

    async def apply_decision(
        self,
        *,
        actor_id: UUID,
        authentication: TrustManagementAuthentication,
        target_provider_id: UUID,
        expected_professional_verification_version: int,
        expected_previous_decision_version: int,
        status: PrescribingEligibilityStatus,
        practitioner_class: PrescribingPractitionerClass,
        source_type: PrescribingEligibilitySourceType,
        source_reference: str,
        evidence_sha256: str,
        decision_reason_code: PrescribingEligibilityReasonCode,
        restriction_code: PrescribingRestrictionCode | None,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> PrescribingEligibilityApplicationResult:
        try:
            key = validate_idempotency_key(idempotency_key)
        except ValueError as exc:
            raise PrescribingEligibilityApplicationError("INVALID_REQUEST") from exc
        if (
            isinstance(expected_professional_verification_version, bool)
            or not isinstance(expected_professional_verification_version, int)
            or expected_professional_verification_version < 1
            or isinstance(expected_previous_decision_version, bool)
            or not isinstance(expected_previous_decision_version, int)
            or expected_previous_decision_version < 0
        ):
            raise PrescribingEligibilityApplicationError("INVALID_REQUEST")

        moment = _aware_utc(now or datetime.now(timezone.utc))
        status = _enum(status, PrescribingEligibilityStatus)
        practitioner_class = _enum(practitioner_class, PrescribingPractitionerClass)
        source_type = _enum(source_type, PrescribingEligibilitySourceType)
        decision_reason_code = _enum(
            decision_reason_code, PrescribingEligibilityReasonCode
        )
        restriction_code = (
            None
            if restriction_code is None
            else _enum(restriction_code, PrescribingRestrictionCode)
        )
        assert isinstance(status, PrescribingEligibilityStatus)
        assert isinstance(practitioner_class, PrescribingPractitionerClass)
        assert isinstance(source_type, PrescribingEligibilitySourceType)
        assert isinstance(decision_reason_code, PrescribingEligibilityReasonCode)
        assert restriction_code is None or isinstance(
            restriction_code, PrescribingRestrictionCode
        )
        _validate_decision_shape(
            status=status,
            practitioner_class=practitioner_class,
            source_type=source_type,
            reason=decision_reason_code,
            restriction=restriction_code,
        )
        source_reference = _clean_source_reference(source_reference)
        evidence_sha256 = _clean_evidence_sha256(evidence_sha256)

        request_hash = _request_hash(
            actor_id=actor_id,
            target_provider_id=target_provider_id,
            expected_professional_verification_version=(
                expected_professional_verification_version
            ),
            expected_previous_decision_version=expected_previous_decision_version,
            status=status,
            practitioner_class=practitioner_class,
            source_type=source_type,
            source_reference=source_reference,
            evidence_sha256=evidence_sha256,
            reason=decision_reason_code,
            restriction=restriction_code,
        )

        try:
            async with self.db.begin():
                existing = (
                    await self.db.execute(
                        _IDEMPOTENCY_SELECT,
                        {
                            "tenant_id": _IDEMPOTENCY_TENANT,
                            "operation": _OPERATION,
                            "key": key,
                        },
                    )
                ).first()
                if existing is not None:
                    return self._replay(existing, expected_hash=request_hash)

                reserved = (
                    await self.db.execute(
                        _IDEMPOTENCY_RESERVE,
                        {
                            "tenant_id": _IDEMPOTENCY_TENANT,
                            "actor_id": str(actor_id),
                            "operation": _OPERATION,
                            "resource_id": str(target_provider_id),
                            "key": key,
                            "request_hash": request_hash,
                        },
                    )
                ).first()
                if reserved is None:
                    existing = (
                        await self.db.execute(
                            _IDEMPOTENCY_SELECT,
                            {
                                "tenant_id": _IDEMPOTENCY_TENANT,
                                "operation": _OPERATION,
                                "key": key,
                            },
                        )
                    ).first()
                    if existing is None:
                        raise PrescribingEligibilityApplicationError(
                            "IDEMPOTENCY_IN_PROGRESS"
                        )
                    return self._replay(existing, expected_hash=request_hash)

                identities = await self._lock_provider_identities(
                    {actor_id, target_provider_id}
                )
                actor = identities.get(actor_id)
                target = identities.get(target_provider_id)
                if target is None:
                    raise PrescribingEligibilityApplicationError("RESOURCE_NOT_FOUND")

                actor_credential, actor_grants = (
                    await self._lock_actor_credential_and_grants(actor_id)
                )
                if actor is not None:
                    actor.credential = actor_credential
                self._authorize_reviewer(
                    actor=actor,
                    actor_grants=actor_grants,
                    authentication=authentication,
                    target_provider_id=target_provider_id,
                    moment=moment,
                )

                professional = (
                    await self.db.execute(
                        select(ProfessionalVerification)
                        .where(
                            ProfessionalVerification.provider_id
                            == target_provider_id
                        )
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                ).scalar_one_or_none()
                if professional is None:
                    raise PrescribingEligibilityApplicationError(
                        "PROFESSIONAL_VERIFICATION_REQUIRED"
                    )
                if professional.version != expected_professional_verification_version:
                    raise PrescribingEligibilityApplicationError(
                        "PROFESSIONAL_VERSION_CONFLICT"
                    )
                if (
                    not professional.registration_authority_code
                    or not professional.registration_number_normalized
                ):
                    raise PrescribingEligibilityApplicationError(
                        "PROFESSIONAL_IDENTITY_BINDING_REQUIRED"
                    )

                latest = (
                    await self.db.execute(
                        select(PrescribingEligibilityDecision)
                        .where(
                            PrescribingEligibilityDecision.provider_id
                            == target_provider_id
                        )
                        .order_by(PrescribingEligibilityDecision.version.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                current_version = latest.version if latest is not None else 0
                if current_version != expected_previous_decision_version:
                    raise PrescribingEligibilityApplicationError(
                        "DECISION_VERSION_CONFLICT"
                    )

                if status is PrescribingEligibilityStatus.ELIGIBLE:
                    _assert_positive_professional_state(professional, moment)

                valid_until = _decision_valid_until(
                    professional,
                    status=status,
                    moment=moment,
                )
                next_version = current_version + 1
                decision_id = uuid4()
                row = PrescribingEligibilityDecision(
                    id=decision_id,
                    provider_id=target_provider_id,
                    professional_verification_id=professional.id,
                    professional_verification_version=professional.version,
                    version=next_version,
                    status=status.value,
                    practitioner_class=practitioner_class.value,
                    source_type=source_type.value,
                    registration_authority_code=(
                        professional.registration_authority_code
                    ),
                    registration_number_normalized=(
                        professional.registration_number_normalized
                    ),
                    source_reference=source_reference,
                    evidence_sha256=evidence_sha256,
                    checked_at=moment,
                    valid_until=valid_until,
                    reviewer_provider_id=actor_id,
                    decision_reason_code=decision_reason_code.value,
                    restriction_code=(
                        restriction_code.value
                        if restriction_code is not None
                        else None
                    ),
                    policy_version=PRESCRIBER_ELIGIBILITY_POLICY_VERSION,
                    previous_decision_id=latest.id if latest is not None else None,
                )
                self.db.add(row)
                await self.db.flush()

                await enqueue_audit_event(
                    self.db,
                    audit_context=AuditContext.platform(
                        domain=AuditDomain.PLATFORM
                    ),
                    idempotency_key=(
                        f"prescribing-eligibility:{decision_id}:{key}"
                    ),
                    actor_id=str(actor_id),
                    event_type=(
                        ProviderTrustAuditEvent
                        .PRESCRIBING_ELIGIBILITY_DECISION_RECORDED.value
                    ),
                    target_id=str(decision_id),
                    patient_id=None,
                    metadata={
                        "authority_code": professional.registration_authority_code,
                        "decision_reason_code": decision_reason_code.value,
                        "decision_version": next_version,
                        "policy_version": PRESCRIBER_ELIGIBILITY_POLICY_VERSION,
                        "practitioner_class": practitioner_class.value,
                        "restriction_code": (
                            restriction_code.value
                            if restriction_code is not None
                            else None
                        ),
                        "source_type": source_type.value,
                        "status": status.value,
                    },
                )

                payload = {
                    "decision_id": str(decision_id),
                    "professional_verification_id": str(professional.id),
                    "provider_id": str(target_provider_id),
                    "practitioner_class": practitioner_class.value,
                    "source_type": source_type.value,
                    "status": status.value,
                    "valid_until": valid_until.isoformat(),
                    "version": next_version,
                }
                await self.db.execute(
                    _IDEMPOTENCY_COMPLETE,
                    {
                        "tenant_id": _IDEMPOTENCY_TENANT,
                        "operation": _OPERATION,
                        "key": key,
                        "payload": json.dumps(
                            payload,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        "version": next_version,
                    },
                )

                return PrescribingEligibilityApplicationResult(
                    decision_id=decision_id,
                    provider_id=target_provider_id,
                    professional_verification_id=professional.id,
                    version=next_version,
                    status=status.value,
                    practitioner_class=practitioner_class.value,
                    source_type=source_type.value,
                    valid_until=valid_until,
                    idempotent_replay=False,
                )
        except PrescribingEligibilityApplicationError:
            raise
        except IntegrityError as exc:
            raise PrescribingEligibilityApplicationError(
                "DECISION_VERSION_CONFLICT"
            ) from exc
        except Exception as exc:
            raise PrescribingEligibilityApplicationError(
                "TRANSACTION_INTEGRITY_FAILURE"
            ) from exc
