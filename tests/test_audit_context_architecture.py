"""Static contracts for trusted, typed audit partitioning."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from app.observability.audit_ledger import append_audit_log, append_audit_log_or_503
from app.security.audit_context import (
    AuditContext,
    AuditContextMissing,
    AuditDomain,
    derive_audit_partition,
)


APP_ROOT = Path(__file__).resolve().parents[1] / "app"
PUBLIC_AUDIT_CALLS = {"append_audit_log", "append_audit_log_or_503"}


def test_public_audit_apis_require_keyword_only_context() -> None:
    for function in (append_audit_log, append_audit_log_or_503):
        signature = inspect.signature(function)
        context = signature.parameters["audit_context"]
        assert context.kind is inspect.Parameter.KEYWORD_ONLY
        assert context.default is inspect.Parameter.empty
        assert "chain_partition" not in signature.parameters


def test_live_audit_calls_always_supply_typed_context() -> None:
    violations: list[str] = []
    for path in APP_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else None
            if name not in PUBLIC_AUDIT_CALLS:
                continue
            if not any(keyword.arg == "audit_context" for keyword in node.keywords):
                violations.append(f"{path.relative_to(APP_ROOT)}:{node.lineno}")
    assert not violations, "Audit calls missing audit_context=: " + ", ".join(violations)


def test_partition_derivation_is_explicit_and_never_implicit_global() -> None:
    assert derive_audit_partition(
        AuditContext.for_tenant(tenant_id="tenant-1", domain=AuditDomain.PATIENT_RECORD)
    ) == "tenant:tenant-1:patient_record"
    assert derive_audit_partition(
        AuditContext.for_hospital(hospital_id="hospital-1", domain=AuditDomain.CONSENT)
    ) == "hospital:hospital-1:consent"
    assert derive_audit_partition(
        AuditContext.platform(domain=AuditDomain.PLATFORM)
    ) == "platform:platform"

    with pytest.raises(
        AuditContextMissing,
        match="Tenant-sensitive audit event requires trusted tenant or hospital context\\.",
    ):
        derive_audit_partition(
            AuditContext(
                tenant_id=None,
                hospital_id=None,
                domain=AuditDomain.CONSENT,
            )
        )

    with pytest.raises(AuditContextMissing):
        AuditContext.platform(domain=AuditDomain.CONSENT)
