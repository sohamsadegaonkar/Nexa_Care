"""Atomic, clinically-untrusted provider account bootstrap.

Registration creates authentication material only.  It deliberately creates
neither a facility trust record nor an active affiliation or clinical role.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.provider import (
    AffiliationTrustStatus,
    AffiliationType,
    HospitalRegistry,
    ProfessionalVerification,
    ProfessionalVerificationStatus,
    ProviderCredential,
    ProviderHospitalAffiliation,
    ProviderIdentity,
)
from app.security.audit_context import AuditContext, AuditDomain
from app.services.audit_outbox import enqueue_audit_event
from app.services.policy_service import validate_idempotency_key
from app.services.provider_auth_service import (
    hash_provider_password,
    normalize_provider_login_identifier,
)

_IDEMPOTENCY_TENANT = "platform-provider-registration"
_IDEMPOTENCY_OPERATION = "provider.bootstrap.v1"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_AUTHORITY_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.-]{0,63}$")
_REGISTRATION_NUMBER_RE = re.compile(r"^[A-Z0-9/]{1,128}$")

_IDEMPOTENCY_SELECT_SQL = text(
    """
    SELECT request_hash, response_status, response_payload
    FROM public.mutation_idempotency
    WHERE tenant_id = :tenant_id
      AND operation = :operation
      AND idempotency_key = :idempotency_key
    """
)
_IDEMPOTENCY_RESERVE_SQL = text(
    """
    INSERT INTO public.mutation_idempotency
        (tenant_id, actor_id, operation, resource_id, idempotency_key, request_hash,
         created_at, retention_expires_at)
    VALUES
        (:tenant_id, :actor_id, :operation, :resource_id, :idempotency_key,
         :request_hash, now(), now() + interval '90 days')
    ON CONFLICT (tenant_id, operation, idempotency_key) DO NOTHING
    RETURNING id
    """
)
_IDEMPOTENCY_COMPLETE_SQL = text(
    """
    UPDATE public.mutation_idempotency
    SET response_status = 201,
        response_payload = CAST(:response_payload AS JSONB),
        resulting_resource_version = 1
    WHERE tenant_id = :tenant_id
      AND operation = :operation
      AND idempotency_key = :idempotency_key
    """
)


class ProviderRegistrationError(RuntimeError):
    """Stable, non-sensitive provider-bootstrap failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ProviderBootstrapRequest:
    display_name: str
    login_identifier: str
    contact_email: str
    contact_phone: str
    password: str
    hospital_id: uuid.UUID
    registration_authority_code: str | None = None
    registration_number: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderBootstrapResult:
    provider_id: str
    idempotent_replay: bool


def normalize_provider_contact_email(value: str) -> str:
    normalized = normalize_provider_login_identifier(value)
    if not _EMAIL_RE.fullmatch(normalized):
        raise ProviderRegistrationError("PROVIDER_REGISTRATION_INVALID_REQUEST")
    return normalized


def normalize_professional_registration_authority_code(value: str) -> str:
    normalized = value.strip().upper()
    if not _AUTHORITY_RE.fullmatch(normalized):
        raise ProviderRegistrationError("PROVIDER_REGISTRATION_INVALID_REQUEST")
    return normalized


def normalize_professional_registration_number(value: str) -> str:
    normalized = re.sub(r"[\s-]+", "", value.strip().upper())
    if not _REGISTRATION_NUMBER_RE.fullmatch(normalized):
        raise ProviderRegistrationError("PROVIDER_REGISTRATION_INVALID_REQUEST")
    return normalized


def _normalize_request(request: ProviderBootstrapRequest) -> ProviderBootstrapRequest:
    display_name = request.display_name.strip()
    if not display_name or len(display_name) > 255:
        raise ProviderRegistrationError("PROVIDER_REGISTRATION_INVALID_REQUEST")
    if not isinstance(request.password, str) or not 12 <= len(request.password) <= 256:
        raise ProviderRegistrationError("PROVIDER_REGISTRATION_INVALID_REQUEST")
    try:
        hospital_id = uuid.UUID(str(request.hospital_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ProviderRegistrationError("PROVIDER_REGISTRATION_INVALID_REQUEST") from exc
    try:
        from app.services.patient_auth_service import normalize_indian_phone

        phone = normalize_indian_phone(request.contact_phone)
    except (TypeError, ValueError) as exc:
        raise ProviderRegistrationError("PROVIDER_REGISTRATION_INVALID_REQUEST") from exc
    authority, number = request.registration_authority_code, request.registration_number
    if (authority is None) != (number is None):
        raise ProviderRegistrationError("PROVIDER_REGISTRATION_INVALID_REQUEST")
    return ProviderBootstrapRequest(
        display_name=display_name,
        login_identifier=normalize_provider_contact_email(request.login_identifier),
        contact_email=normalize_provider_contact_email(request.contact_email),
        contact_phone=phone,
        password=request.password,
        hospital_id=hospital_id,
        registration_authority_code=(
            normalize_professional_registration_authority_code(authority)
            if authority is not None
            else None
        ),
        registration_number=(
            normalize_professional_registration_number(number)
            if number is not None
            else None
        ),
    )


def canonical_provider_bootstrap_request_hash(
    request: ProviderBootstrapRequest, *, hmac_secret: str
) -> str:
    """Produce a durable, password-safe canonical registration fingerprint."""

    if len(hmac_secret.encode("utf-8")) < 32:
        raise ProviderRegistrationError("PROVIDER_REGISTRATION_UNAVAILABLE")
    normalized = _normalize_request(request)
    password_fingerprint = hmac.new(
        hmac_secret.encode("utf-8"),
        b"provider-bootstrap-password-v1:\x00" + normalized.password.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    canonical = json.dumps(
        {
            "contact_email": normalized.contact_email,
            "contact_phone": normalized.contact_phone,
            "display_name": normalized.display_name,
            "hospital_id": str(normalized.hospital_id),
            "login_identifier": normalized.login_identifier,
            "password_fingerprint": password_fingerprint,
            "registration_authority_code": normalized.registration_authority_code,
            "registration_number_normalized": normalized.registration_number,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _result_from_completed_idempotency(row, request_hash: str) -> ProviderBootstrapResult:
    if row is None:
        raise ProviderRegistrationError("PROVIDER_REGISTRATION_IN_PROGRESS")
    mapping = row._mapping
    if mapping["request_hash"] != request_hash:
        raise ProviderRegistrationError("IDEMPOTENCY_KEY_REUSED")
    payload = mapping["response_payload"]
    if mapping["response_status"] != 201 or not isinstance(payload, dict):
        raise ProviderRegistrationError("PROVIDER_REGISTRATION_IN_PROGRESS")
    provider_id = payload.get("provider_id")
    try:
        provider_id = str(uuid.UUID(str(provider_id)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ProviderRegistrationError("PROVIDER_REGISTRATION_UNAVAILABLE") from exc
    return ProviderBootstrapResult(provider_id=provider_id, idempotent_replay=True)


async def bootstrap_provider_account(
    db: AsyncSession,
    *,
    request: ProviderBootstrapRequest,
    idempotency_key: str,
    idempotency_hmac_secret: str,
) -> ProviderBootstrapResult:
    """Atomically create an authentication-capable but untrusted provider.

    The outer transaction owns idempotency reservation/completion, all three
    graph rows, and the required audit-outbox record.  Any error rolls all
    components back together.
    """

    try:
        idempotency_key = validate_idempotency_key(idempotency_key)
    except ValueError as exc:
        raise ProviderRegistrationError("PROVIDER_REGISTRATION_INVALID_REQUEST") from exc
    normalized = _normalize_request(request)
    request_hash = canonical_provider_bootstrap_request_hash(
        normalized, hmac_secret=idempotency_hmac_secret
    )

    async with db.begin():
        existing = (
            await db.execute(
                _IDEMPOTENCY_SELECT_SQL,
                {
                    "tenant_id": _IDEMPOTENCY_TENANT,
                    "operation": _IDEMPOTENCY_OPERATION,
                    "idempotency_key": idempotency_key,
                },
            )
        ).first()
        if existing is not None:
            return _result_from_completed_idempotency(existing, request_hash)

        reserved = (
            await db.execute(
                _IDEMPOTENCY_RESERVE_SQL,
                {
                    "tenant_id": _IDEMPOTENCY_TENANT,
                    "actor_id": "PROVIDER_REGISTRATION",
                    "operation": _IDEMPOTENCY_OPERATION,
                    "resource_id": "provider-bootstrap",
                    "idempotency_key": idempotency_key,
                    "request_hash": request_hash,
                },
            )
        ).first()
        if reserved is None:
            existing = (
                await db.execute(
                    _IDEMPOTENCY_SELECT_SQL,
                    {
                        "tenant_id": _IDEMPOTENCY_TENANT,
                        "operation": _IDEMPOTENCY_OPERATION,
                        "idempotency_key": idempotency_key,
                    },
                )
            ).first()
            return _result_from_completed_idempotency(existing, request_hash)

        provider = ProviderIdentity(
            provider_uid=str(uuid.uuid4()),
            display_name=normalized.display_name,
            contact_email=normalized.contact_email,
            contact_phone=normalized.contact_phone,
            role="provider",
            status="active",
            is_active=True,
            email_verified_at=None,
            phone_verified_at=None,
        )
        try:
            async with db.begin_nested():
                db.add(provider)
                await db.flush()
                credential = ProviderCredential(
                    provider_id=provider.id,
                    provider_uid=provider.provider_uid,
                    login_identifier=normalized.login_identifier,
                    password_hash=hash_provider_password(normalized.password),
                    mfa_enabled=False,
                    is_active=True,
                )
                professional = ProfessionalVerification(
                    provider_id=provider.id,
                    registration_authority_code=normalized.registration_authority_code,
                    registration_number_normalized=normalized.registration_number,
                    status=ProfessionalVerificationStatus.NOT_SUBMITTED.value,
                    previous_verification_valid=False,
                )
                hospital = await db.scalar(
                    select(HospitalRegistry).where(
                        HospitalRegistry.id == normalized.hospital_id,
                        HospitalRegistry.is_active.is_(True),
                    )
                )
                if hospital is None:
                    raise ProviderRegistrationError(
                        "PROVIDER_REGISTRATION_FACILITY_UNAVAILABLE"
                    )
                affiliation = ProviderHospitalAffiliation(
                    provider_id=provider.id,
                    hospital_id=hospital.id,
                    affiliation_type=AffiliationType.PERMANENT.value,
                    roles=[],
                    is_primary=True,
                    is_active=True,
                    trust_status=AffiliationTrustStatus.PENDING_ACTIVATION.value,
                )
                db.add_all([credential, professional, affiliation])
                await db.flush()
        except IntegrityError as exc:
            # The client receives one stable recovery-oriented outcome whether
            # the collision was login, contact email, UID, or professional ID.
            raise ProviderRegistrationError("PROVIDER_REGISTRATION_CONFLICT") from exc

        safe_response = {"provider_id": str(provider.id), "status": "registered"}
        await enqueue_audit_event(
            db,
            audit_context=AuditContext.platform(domain=AuditDomain.AUTH),
            idempotency_key=f"provider-registration:{provider.id}",
            actor_id=str(provider.id),
            event_type="PROVIDER_ACCOUNT_BOOTSTRAPPED",
            target_id=str(provider.id),
            patient_id=None,
            metadata={
                "professional_verification_status": (
                    ProfessionalVerificationStatus.NOT_SUBMITTED.value
                ),
                "affiliation_trust_status": (
                    AffiliationTrustStatus.PENDING_ACTIVATION.value
                ),
                "clinical_authority": "not_granted",
            },
        )
        await db.execute(
            _IDEMPOTENCY_COMPLETE_SQL,
            {
                "tenant_id": _IDEMPOTENCY_TENANT,
                "operation": _IDEMPOTENCY_OPERATION,
                "idempotency_key": idempotency_key,
                "response_payload": json.dumps(
                    safe_response, sort_keys=True, separators=(",", ":")
                ),
            },
        )
        return ProviderBootstrapResult(
            provider_id=str(provider.id), idempotent_replay=False
        )
