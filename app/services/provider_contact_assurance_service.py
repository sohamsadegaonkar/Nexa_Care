"""Authoritative provider-owned email and phone contact assurance.

Redis may throttle delivery, but PostgreSQL challenge lifecycle, exact-contact
binding, idempotency, and audit outbox state remain the sole authority.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.provider import ProviderContactVerificationChallenge, ProviderIdentity
from app.security.audit_context import AuditContext
from app.services.audit_outbox import enqueue_audit_event
from app.services.policy_service import validate_idempotency_key
from app.services.provider_registration_service import normalize_provider_contact_email

_PURPOSE = "SELF_CONTACT_ASSURANCE_V1"
_TTL = timedelta(minutes=15)
_MAX_ATTEMPTS = 5
_IDEMPOTENCY_SELECT_SQL = text(
    """
    SELECT request_hash, response_status, response_payload
    FROM public.mutation_idempotency
    WHERE tenant_id = :tenant_id AND operation = :operation
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
    SET response_status = :response_status,
        response_payload = CAST(:response_payload AS JSONB),
        resulting_resource_version = 1
    WHERE tenant_id = :tenant_id AND operation = :operation
      AND idempotency_key = :idempotency_key
    """
)


class ProviderContactAssuranceError(RuntimeError):
    """Stable, value-free contact-assurance failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ProviderContactChallengeTransport(Protocol):
    """Delivery seam. Production integrations must never return a verifier."""

    def assert_ready(self) -> None: ...

    async def deliver(self, *, channel: str, destination: str, verifier: str) -> None: ...


class UnavailableProviderContactChallengeTransport:
    """Fail closed until an approved email/SMS adapter is configured."""

    def assert_ready(self) -> None:
        raise ProviderContactAssuranceError("CONTACT_CHALLENGE_DELIVERY_UNAVAILABLE")

    async def deliver(self, *, channel: str, destination: str, verifier: str) -> None:
        del channel, destination, verifier
        self.assert_ready()


def get_provider_contact_challenge_transport() -> ProviderContactChallengeTransport:
    """Return the production-safe default; tests inject a controlled adapter."""

    return UnavailableProviderContactChallengeTransport()


@dataclass(frozen=True, slots=True)
class IssuedProviderContactChallenge:
    challenge_id: uuid.UUID
    channel: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ProviderContactMutationResult:
    channel: str
    idempotent_replay: bool


def _tenant(provider_id: uuid.UUID) -> str:
    return f"provider-contact:{provider_id}"


def _hmac(secret: str, domain: str, value: str) -> str:
    if len(secret.encode("utf-8")) < 32:
        raise ProviderContactAssuranceError("CONTACT_ASSURANCE_UNAVAILABLE")
    return hmac.new(
        secret.encode("utf-8"), f"{domain}:\x00{value}".encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _normalized_contact(channel: str, value: str) -> str:
    try:
        if channel == "EMAIL":
            return normalize_provider_contact_email(value)
        if channel == "PHONE":
            from app.services.patient_auth_service import normalize_indian_phone

            return normalize_indian_phone(value)
    except (TypeError, ValueError, ProviderContactAssuranceError) as exc:
        raise ProviderContactAssuranceError("CONTACT_ASSURANCE_INVALID_REQUEST") from exc
    raise ProviderContactAssuranceError("CONTACT_ASSURANCE_INVALID_REQUEST")


def _request_hash(secret: str, operation: str, values: dict[str, str]) -> str:
    canonical = json.dumps(values, sort_keys=True, separators=(",", ":"))
    return _hmac(secret, f"provider-contact-idempotency:{operation}", canonical)


def _idempotency_replay(row, request_hash: str) -> ProviderContactMutationResult | None:
    if row is None:
        return None
    mapping = row._mapping
    if mapping["request_hash"] != request_hash:
        raise ProviderContactAssuranceError("IDEMPOTENCY_KEY_REUSED")
    payload = mapping["response_payload"]
    if mapping["response_status"] == 200 and isinstance(payload, dict):
        channel = payload.get("channel")
        if channel in {"EMAIL", "PHONE"}:
            return ProviderContactMutationResult(channel=channel, idempotent_replay=True)
    if mapping["response_status"] == 400:
        raise ProviderContactAssuranceError("CONTACT_CHALLENGE_VERIFICATION_FAILED")
    raise ProviderContactAssuranceError("CONTACT_ASSURANCE_IN_PROGRESS")


async def _reserve_idempotency(
    db: AsyncSession,
    *,
    provider_id: uuid.UUID,
    operation: str,
    idempotency_key: str,
    request_hash: str,
    resource_id: str,
) -> ProviderContactMutationResult | None:
    try:
        idempotency_key = validate_idempotency_key(idempotency_key)
    except ValueError as exc:
        raise ProviderContactAssuranceError("CONTACT_ASSURANCE_INVALID_REQUEST") from exc
    params = {
        "tenant_id": _tenant(provider_id),
        "operation": operation,
        "idempotency_key": idempotency_key,
    }
    existing = (await db.execute(_IDEMPOTENCY_SELECT_SQL, params)).first()
    replay = _idempotency_replay(existing, request_hash)
    if replay is not None:
        return replay
    reserved = (
        await db.execute(
            _IDEMPOTENCY_RESERVE_SQL,
            {
                **params,
                "actor_id": str(provider_id),
                "resource_id": resource_id,
                "request_hash": request_hash,
            },
        )
    ).first()
    if reserved is not None:
        return None
    existing = (await db.execute(_IDEMPOTENCY_SELECT_SQL, params)).first()
    replay = _idempotency_replay(existing, request_hash)
    if replay is not None:
        return replay
    raise ProviderContactAssuranceError("CONTACT_ASSURANCE_IN_PROGRESS")


async def _complete_idempotency(
    db: AsyncSession,
    *,
    provider_id: uuid.UUID,
    operation: str,
    idempotency_key: str,
    response_status: int,
    payload: dict[str, object],
) -> None:
    await db.execute(
        _IDEMPOTENCY_COMPLETE_SQL,
        {
            "tenant_id": _tenant(provider_id),
            "operation": operation,
            "idempotency_key": idempotency_key,
            "response_status": response_status,
            "response_payload": json.dumps(payload, sort_keys=True, separators=(",", ":")),
        },
    )


async def issue_provider_contact_challenge(
    db: AsyncSession,
    *,
    provider_id: uuid.UUID,
    channel: str,
    hmac_secret: str,
    audit_context: AuditContext,
    transport: ProviderContactChallengeTransport,
) -> IssuedProviderContactChallenge:
    """Replace an outstanding challenge and atomically record its lifecycle."""

    if channel not in {"EMAIL", "PHONE"}:
        raise ProviderContactAssuranceError("CONTACT_ASSURANCE_INVALID_REQUEST")
    transport.assert_ready()
    verifier = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    async with db.begin():
        provider = (
            await db.execute(
                select(ProviderIdentity)
                .where(ProviderIdentity.id == provider_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if provider is None:
            raise ProviderContactAssuranceError("CONTACT_ASSURANCE_DENIED")
        destination = _normalized_contact(
            channel,
            provider.contact_email if channel == "EMAIL" else provider.contact_phone,
        )
        binding = _hmac(hmac_secret, f"provider-contact-binding:{channel}", destination)
        await db.execute(
            update(ProviderContactVerificationChallenge)
            .where(
                ProviderContactVerificationChallenge.provider_id == provider_id,
                ProviderContactVerificationChallenge.channel == channel,
                ProviderContactVerificationChallenge.purpose == _PURPOSE,
                ProviderContactVerificationChallenge.consumed_at.is_(None),
                ProviderContactVerificationChallenge.invalidated_at.is_(None),
            )
            .values(invalidated_at=now)
        )
        challenge = ProviderContactVerificationChallenge(
            provider_id=provider_id,
            channel=channel,
            purpose=_PURPOSE,
            contact_binding_hmac=binding,
            verifier_hmac=_hmac(hmac_secret, "provider-contact-verifier", verifier),
            expires_at=now + _TTL,
            max_attempts=_MAX_ATTEMPTS,
            failed_attempt_count=0,
        )
        db.add(challenge)
        await db.flush()
        await transport.deliver(
            channel=channel, destination=destination, verifier=verifier
        )
        await enqueue_audit_event(
            db,
            audit_context=audit_context,
            idempotency_key=f"provider-contact-challenge:{challenge.id}",
            actor_id=str(provider_id),
            event_type="PROVIDER_CONTACT_CHALLENGE_ISSUED",
            target_id=str(provider_id),
            patient_id=None,
            metadata={"channel": channel, "purpose": _PURPOSE},
        )
    return IssuedProviderContactChallenge(
        challenge_id=challenge.id, channel=channel, expires_at=challenge.expires_at
    )


async def verify_provider_contact_challenge(
    db: AsyncSession,
    *,
    provider_id: uuid.UUID,
    channel: str,
    challenge_id: uuid.UUID,
    verifier: str,
    idempotency_key: str,
    hmac_secret: str,
    audit_context: AuditContext,
) -> ProviderContactMutationResult:
    """Consume a current matching challenge and set only its matching timestamp."""

    if channel not in {"EMAIL", "PHONE"} or not isinstance(verifier, str):
        raise ProviderContactAssuranceError("CONTACT_ASSURANCE_INVALID_REQUEST")
    operation = f"provider.contact.verify.{channel.lower()}.v1"
    request_hash = _request_hash(
        hmac_secret,
        operation,
        {"challenge_id": str(challenge_id), "verifier": verifier},
    )
    failure = False
    async with db.begin():
        replay = await _reserve_idempotency(
            db,
            provider_id=provider_id,
            operation=operation,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            resource_id=str(challenge_id),
        )
        if replay is not None:
            return replay
        provider = (
            await db.execute(
                select(ProviderIdentity)
                .where(ProviderIdentity.id == provider_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        challenge = (
            await db.execute(
                select(ProviderContactVerificationChallenge)
                .where(ProviderContactVerificationChallenge.id == challenge_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        now = datetime.now(timezone.utc)
        if provider is None or challenge is None or challenge.provider_id != provider_id:
            failure = True
        elif (
            challenge.channel != channel
            or challenge.purpose != _PURPOSE
            or challenge.consumed_at is not None
            or challenge.invalidated_at is not None
            or challenge.expires_at <= now
            or challenge.failed_attempt_count >= challenge.max_attempts
        ):
            # Preserve the first terminal-consumption timestamp on later
            # retries; an older challenge must not acquire a new lifecycle
            # moment merely because a different idempotency key was used.
            if challenge.consumed_at is None:
                challenge.consumed_at = now
            failure = True
        else:
            destination = _normalized_contact(
                channel,
                provider.contact_email if channel == "EMAIL" else provider.contact_phone,
            )
            expected_binding = _hmac(
                hmac_secret, f"provider-contact-binding:{channel}", destination
            )
            actual_verifier = _hmac(hmac_secret, "provider-contact-verifier", verifier)
            if not (
                hmac.compare_digest(challenge.contact_binding_hmac, expected_binding)
                and hmac.compare_digest(challenge.verifier_hmac, actual_verifier)
            ):
                challenge.failed_attempt_count += 1
                if challenge.failed_attempt_count >= challenge.max_attempts:
                    challenge.consumed_at = now
                failure = True
            else:
                challenge.consumed_at = now
                challenge.succeeded_at = now
                if channel == "EMAIL":
                    provider.email_verified_at = now
                    event_type = "PROVIDER_CONTACT_EMAIL_VERIFIED"
                else:
                    provider.phone_verified_at = now
                    event_type = "PROVIDER_CONTACT_PHONE_VERIFIED"
                await enqueue_audit_event(
                    db,
                    audit_context=audit_context,
                    idempotency_key=f"provider-contact-verified:{challenge.id}",
                    actor_id=str(provider_id),
                    event_type=event_type,
                    target_id=str(provider_id),
                    patient_id=None,
                    metadata={"channel": channel, "purpose": _PURPOSE},
                )
        await _complete_idempotency(
            db,
            provider_id=provider_id,
            operation=operation,
            idempotency_key=idempotency_key,
            response_status=400 if failure else 200,
            payload={"channel": channel, "verified": not failure},
        )
    if failure:
        raise ProviderContactAssuranceError("CONTACT_CHALLENGE_VERIFICATION_FAILED")
    return ProviderContactMutationResult(channel=channel, idempotent_replay=False)


async def update_provider_contact(
    db: AsyncSession,
    *,
    provider_id: uuid.UUID,
    channel: str,
    value: str,
    idempotency_key: str,
    hmac_secret: str,
    audit_context: AuditContext,
) -> ProviderContactMutationResult:
    """Replace a self-contact value and reset only its verification assurance."""

    normalized = _normalized_contact(channel, value)
    operation = f"provider.contact.update.{channel.lower()}.v1"
    request_hash = _request_hash(hmac_secret, operation, {"contact": normalized})
    try:
        async with db.begin():
            replay = await _reserve_idempotency(
                db,
                provider_id=provider_id,
                operation=operation,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                resource_id=str(provider_id),
            )
            if replay is not None:
                return replay
            provider = (
                await db.execute(
                    select(ProviderIdentity)
                    .where(ProviderIdentity.id == provider_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if provider is None:
                raise ProviderContactAssuranceError("CONTACT_ASSURANCE_DENIED")
            current = provider.contact_email if channel == "EMAIL" else provider.contact_phone
            changed = current != normalized
            if changed:
                if channel == "EMAIL":
                    provider.contact_email = normalized
                    provider.email_verified_at = None
                    event_type = "PROVIDER_CONTACT_EMAIL_CHANGED"
                else:
                    provider.contact_phone = normalized
                    provider.phone_verified_at = None
                    event_type = "PROVIDER_CONTACT_PHONE_CHANGED"
                await db.execute(
                    update(ProviderContactVerificationChallenge)
                    .where(
                        ProviderContactVerificationChallenge.provider_id == provider_id,
                        ProviderContactVerificationChallenge.channel == channel,
                        ProviderContactVerificationChallenge.consumed_at.is_(None),
                        ProviderContactVerificationChallenge.invalidated_at.is_(None),
                    )
                    .values(invalidated_at=datetime.now(timezone.utc))
                )
                await enqueue_audit_event(
                    db,
                    audit_context=audit_context,
                    idempotency_key=(
                        f"provider-contact-changed:{provider_id}:{channel}:"
                        f"{request_hash[:24]}"
                    ),
                    actor_id=str(provider_id),
                    event_type=event_type,
                    target_id=str(provider_id),
                    patient_id=None,
                    metadata={"channel": channel, "verification_reset": True},
                )
            await _complete_idempotency(
                db,
                provider_id=provider_id,
                operation=operation,
                idempotency_key=idempotency_key,
                response_status=200,
                payload={"channel": channel, "changed": changed},
            )
    except IntegrityError as exc:
        raise ProviderContactAssuranceError("CONTACT_ASSURANCE_CONFLICT") from exc
    return ProviderContactMutationResult(channel=channel, idempotent_replay=False)
