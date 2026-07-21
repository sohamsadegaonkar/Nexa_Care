"""Trusted audit partition context.

Only authenticated dependencies, server-side tenancy resolution, or explicit
platform operations may construct these contexts. Client metadata is never
consulted when deriving a partition.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from enum import Enum


class AuditDomain(str, Enum):
    AUTH = "auth"
    CONSENT = "consent"
    PATIENT_RECORD = "patient_record"
    EMERGENCY = "emergency"
    POLICY = "policy"
    PIPELINE = "pipeline"
    NFC = "nfc"
    MERGE = "merge"
    ERASURE = "erasure"
    PLATFORM = "platform"


class AuditContextMissing(RuntimeError):
    """Raised when a tenant-sensitive event lacks trusted server context."""


@dataclass(frozen=True, slots=True)
class AuditContext:
    tenant_id: str | None
    hospital_id: str | None
    domain: AuditDomain
    platform_global: bool = False

    @classmethod
    def for_tenant(cls, *, tenant_id: str, domain: AuditDomain) -> "AuditContext":
        if not str(tenant_id).strip():
            raise AuditContextMissing("Trusted tenant ID is required.")
        return cls(tenant_id=str(tenant_id), hospital_id=None, domain=domain)

    @classmethod
    def for_hospital(cls, *, hospital_id: str, domain: AuditDomain) -> "AuditContext":
        if not str(hospital_id).strip():
            raise AuditContextMissing("Trusted hospital ID is required.")
        return cls(tenant_id=None, hospital_id=str(hospital_id), domain=domain)

    @classmethod
    def platform(cls, *, domain: AuditDomain) -> "AuditContext":
        if domain not in {AuditDomain.AUTH, AuditDomain.PLATFORM}:
            raise AuditContextMissing("Only approved platform domains may be global.")
        return cls(tenant_id=None, hospital_id=None, domain=domain, platform_global=True)


def derive_audit_partition(context: AuditContext) -> str:
    if context.tenant_id:
        return f"tenant:{context.tenant_id}:{context.domain.value}"
    if context.hospital_id:
        return f"hospital:{context.hospital_id}:{context.domain.value}"
    if context.platform_global and context.domain in {AuditDomain.AUTH, AuditDomain.PLATFORM}:
        return f"platform:{context.domain.value}"
    raise AuditContextMissing(
        "Tenant-sensitive audit event requires trusted tenant or hospital context."
    )


_trusted_audit_scope: ContextVar[tuple[str, str] | None] = ContextVar(
    "trusted_audit_scope", default=None
)


def bind_trusted_audit_hospital(hospital_id: str) -> Token:
    return _trusted_audit_scope.set(("hospital", str(hospital_id)))


def bind_trusted_audit_tenant(tenant_id: str) -> Token:
    return _trusted_audit_scope.set(("tenant", str(tenant_id)))


def reset_trusted_audit_scope(token: Token) -> None:
    _trusted_audit_scope.reset(token)


def current_audit_context(domain: AuditDomain) -> AuditContext:
    scope = _trusted_audit_scope.get()
    if scope is not None:
        kind, identifier = scope
        if kind == "tenant":
            return AuditContext.for_tenant(tenant_id=identifier, domain=domain)
        return AuditContext.for_hospital(hospital_id=identifier, domain=domain)
    if domain in {AuditDomain.AUTH, AuditDomain.PLATFORM}:
        return AuditContext.platform(domain=domain)
    raise AuditContextMissing(
        "Tenant-sensitive audit event requires trusted tenant or hospital context."
    )
