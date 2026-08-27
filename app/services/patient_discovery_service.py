"""Server-authoritative, minimum-disclosure patient discovery primitives."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient
from app.security.erasure_registry import (
    ErasureRegistryUnavailable,
    _PatientErasedSignal,
    check_erasure_registry,
)
from app.services.card_redirect_service import (
    CardRedirectService,
    TombstoneIntegrityError,
)

PUBLIC_ID_PREFIX = "NC-"
PUBLIC_ID_RANDOM_BYTES = 12  # 96 bits
PUBLIC_ID_RE = re.compile(r"^NC-[A-F0-9]{24}$")
DISCOVERY_HANDLE_TTL_SECONDS = (
    120  # engineering provisional, not a legal retention period
)
_HANDLE_PREFIX = "nexa:patient_discovery:"
_PENDING_AUDIT = "PENDING_AUDIT"
_ACTIVE = "ACTIVE"


class DiscoveryUnavailable(RuntimeError):
    pass


class DiscoveryNoMatch(RuntimeError):
    pass


class DiscoveryHandleInvalid(RuntimeError):
    pass


@dataclass(frozen=True)
class DiscoveryHandle:
    value: str
    expires_at: datetime


def generate_public_patient_id() -> str:
    """Generate a printable opaque identifier with at least 96 random bits."""
    return PUBLIC_ID_PREFIX + secrets.token_hex(PUBLIC_ID_RANDOM_BYTES).upper()


def normalize_public_patient_id(value: str) -> str:
    normalized = value.strip().upper()
    if not PUBLIC_ID_RE.fullmatch(normalized):
        raise ValueError("INVALID_PUBLIC_PATIENT_ID")
    return normalized


def _handle_key(raw_handle: str) -> str:
    return _HANDLE_PREFIX + hashlib.sha256(raw_handle.encode("utf-8")).hexdigest()


_CONSUME_LUA = """
local raw = redis.call('GET', KEYS[1])
if not raw then return false end
local data = cjson.decode(raw)
if data.state ~= 'ACTIVE' then return false end
redis.call('DEL', KEYS[1])
return raw
"""


_ACTIVATE_LUA = """
local raw = redis.call('GET', KEYS[1])
if not raw then return 0 end
local data = cjson.decode(raw)
if data.state ~= 'PENDING_AUDIT' then return 0 end
local ttl = redis.call('PTTL', KEYS[1])
if ttl <= 0 then return 0 end
data.state = 'ACTIVE'
redis.call('SET', KEYS[1], cjson.encode(data), 'PX', ttl)
return 1
"""


class PatientDiscoveryService:
    def __init__(self, db: AsyncSession, redis) -> None:
        self.db = db
        self.redis = redis

    async def resolve_public_id(self, value: str) -> tuple[Patient, bool]:
        public_id = normalize_public_patient_id(value)
        patient = await self.db.scalar(
            select(Patient).where(Patient.public_patient_id == public_id)
        )
        if patient is None:
            raise DiscoveryNoMatch()
        return await self.resolve_patient_id(patient.patient_uuid)

    async def resolve_patient_id(self, patient_id: UUID) -> tuple[Patient, bool]:
        """Resolve a matched identifier to one active, unerased canonical patient."""
        try:
            redirect = await CardRedirectService(self.db).resolve_patient_with_redirect(
                patient_id
            )
        except TombstoneIntegrityError as exc:
            raise DiscoveryUnavailable() from exc
        canonical_id = UUID(str(redirect["canonical_patient_uuid"]))
        patient = await self.db.get(Patient, canonical_id)
        if patient is None or patient.is_deleted:
            raise DiscoveryNoMatch()
        try:
            await check_erasure_registry(str(canonical_id), self.db)
        except _PatientErasedSignal as exc:
            raise DiscoveryNoMatch() from exc
        except ErasureRegistryUnavailable as exc:
            raise DiscoveryUnavailable() from exc
        return patient, bool(redirect.get("is_redirected"))

    async def issue_handle(
        self,
        *,
        patient: Patient,
        provider_id: str,
        hospital_id: str,
        session_binding: str | None,
        identifier_type: str,
    ) -> DiscoveryHandle:
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=DISCOVERY_HANDLE_TTL_SECONDS)
        payload = {
            # A discovered patient is not an authorization capability until
            # the mandatory terminal audit has completed and activation wins.
            "state": _PENDING_AUDIT,
            "patient_id": str(patient.patient_uuid),
            "provider_id": provider_id,
            "hospital_id": hospital_id,
            "session_binding": session_binding,
            "identifier_type": identifier_type,
            "issued_at": now.isoformat(),
        }
        try:
            for _ in range(3):
                raw = secrets.token_urlsafe(32)
                stored = await self.redis.set(
                    _handle_key(raw),
                    json.dumps(payload),
                    nx=True,
                    ex=DISCOVERY_HANDLE_TTL_SECONDS,
                )
                if stored:
                    return DiscoveryHandle(raw, expires)
        except Exception as exc:
            raise DiscoveryUnavailable() from exc
        raise DiscoveryUnavailable()

    async def activate_handle(self, *, raw_handle: str) -> bool:
        """Atomically promote one audited discovery handle without extending TTL."""
        try:
            result = await self.redis.eval(_ACTIVATE_LUA, 1, _handle_key(raw_handle))
        except Exception as exc:
            raise DiscoveryUnavailable() from exc
        return int(result) == 1

    async def consume_handle(
        self,
        *,
        raw_handle: str,
        provider_id: str,
        hospital_id: str,
        session_binding: str | None,
    ) -> Patient:
        try:
            raw = await self.redis.eval(_CONSUME_LUA, 1, _handle_key(raw_handle))
        except Exception as exc:
            raise DiscoveryUnavailable() from exc
        if not raw:
            raise DiscoveryHandleInvalid()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            data = json.loads(raw)
            if (
                not secrets.compare_digest(str(data["provider_id"]), provider_id)
                or not secrets.compare_digest(str(data["hospital_id"]), hospital_id)
                or not secrets.compare_digest(
                    str(data.get("session_binding") or ""), str(session_binding or "")
                )
            ):
                raise DiscoveryHandleInvalid()
            patient, _ = await self.resolve_patient_id(UUID(str(data["patient_id"])))
            return patient
        except DiscoveryHandleInvalid:
            raise
        except (ValueError, KeyError, TypeError) as exc:
            raise DiscoveryHandleInvalid() from exc

    async def revoke_handle(self, *, raw_handle: str) -> None:
        """Remove a staged handle before it can be disclosed without an audit."""
        try:
            await self.redis.delete(_handle_key(raw_handle))
        except Exception as exc:
            raise DiscoveryUnavailable() from exc
