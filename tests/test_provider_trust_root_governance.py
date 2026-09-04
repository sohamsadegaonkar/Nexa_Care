"""Unit tests for offline root-of-trust governance service and operator CLI."""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.main import app
from app.services.provider_trust_root_governance import (
    OPERATOR_REVOCATION_REASONS,
    ProviderTrustRootGovernanceError,
    ProviderTrustRootGovernanceService,
    RootRevocationReasonCode,
    _validate_and_normalize_evidence,
)
from scripts.governance_trust_root import (
    _build_parser,
    _preflight_guards,
    run_governance,
)

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# 1. Evidence validation & normalization
# ---------------------------------------------------------------------------


def test_evidence_normalization_and_validation() -> None:
    # Valid evidence
    op, appr, gov = _validate_and_normalize_evidence(
        "  operator_1  ", "  approver_2  ", "  CAB-2026-001  "
    )
    assert op == "operator_1"
    assert appr == "approver_2"
    assert gov == "CAB-2026-001"

    # Diverse governance references accepted without mandatory prefixes
    for valid_ref in [
        "CAB-2026-091",
        "INC-2026-014",
        "ROOT-ROTATION-Q4",
        "SECURITY-RESPONSE-118",
        "CHANGE-REQUEST-77",
        "annual-rotation-2026",
    ]:
        _, _, ref = _validate_and_normalize_evidence("op", "appr", valid_ref)
        assert ref == valid_ref

    # Operator == Approver prohibited
    with pytest.raises(ProviderTrustRootGovernanceError) as exc:
        _validate_and_normalize_evidence("same_person", "same_person", "REF-1")
    assert exc.value.code == "INVALID_REQUEST"

    # Blank / whitespace only prohibited
    with pytest.raises(ProviderTrustRootGovernanceError) as exc:
        _validate_and_normalize_evidence("   ", "approver", "REF-1")
    assert exc.value.code == "INVALID_REQUEST"

    with pytest.raises(ProviderTrustRootGovernanceError) as exc:
        _validate_and_normalize_evidence("operator", "   ", "REF-1")
    assert exc.value.code == "INVALID_REQUEST"

    with pytest.raises(ProviderTrustRootGovernanceError) as exc:
        _validate_and_normalize_evidence("operator", "approver", "   ")
    assert exc.value.code == "INVALID_REQUEST"

    # > 128 chars prohibited
    long_str = "x" * 129
    with pytest.raises(ProviderTrustRootGovernanceError) as exc:
        _validate_and_normalize_evidence(long_str, "approver", "REF-1")
    assert exc.value.code == "INVALID_REQUEST"

    with pytest.raises(ProviderTrustRootGovernanceError) as exc:
        _validate_and_normalize_evidence("operator", long_str, "REF-1")
    assert exc.value.code == "INVALID_REQUEST"

    with pytest.raises(ProviderTrustRootGovernanceError) as exc:
        _validate_and_normalize_evidence("operator", "approver", long_str)
    assert exc.value.code == "INVALID_REQUEST"


# ---------------------------------------------------------------------------
# 2. Bounded root validity checks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grant_root_valid_until_bounds() -> None:
    now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
    service = ProviderTrustRootGovernanceService(MagicMock())

    # Naive datetime rejected
    naive_dt = datetime(2026, 9, 10, 12, 0, 0)
    with pytest.raises(ProviderTrustRootGovernanceError) as exc:
        await service.grant_root(
            operator_actor_id="op1",
            approver_actor_id="appr1",
            target_provider_id=uuid4(),
            valid_until=naive_dt,
            expected_active_root_count=0,
            governance_reference="REF-1",
            idempotency_key=str(uuid4()),
            now=now,
        )
    assert exc.value.code == "INVALID_REQUEST"

    # Past / now datetime rejected
    past_dt = now - timedelta(seconds=1)
    with pytest.raises(ProviderTrustRootGovernanceError) as exc:
        await service.grant_root(
            operator_actor_id="op1",
            approver_actor_id="appr1",
            target_provider_id=uuid4(),
            valid_until=past_dt,
            expected_active_root_count=0,
            governance_reference="REF-1",
            idempotency_key=str(uuid4()),
            now=now,
        )
    assert exc.value.code == "INVALID_REQUEST"

    with pytest.raises(ProviderTrustRootGovernanceError) as exc:
        await service.grant_root(
            operator_actor_id="op1",
            approver_actor_id="appr1",
            target_provider_id=uuid4(),
            valid_until=now,
            expected_active_root_count=0,
            governance_reference="REF-1",
            idempotency_key=str(uuid4()),
            now=now,
        )
    assert exc.value.code == "INVALID_REQUEST"

    # Greater than 90 days rejected
    too_long_dt = now + timedelta(days=90, seconds=1)
    with pytest.raises(ProviderTrustRootGovernanceError) as exc:
        await service.grant_root(
            operator_actor_id="op1",
            approver_actor_id="appr1",
            target_provider_id=uuid4(),
            valid_until=too_long_dt,
            expected_active_root_count=0,
            governance_reference="REF-1",
            idempotency_key=str(uuid4()),
            now=now,
        )
    assert exc.value.code == "INVALID_REQUEST"


# ---------------------------------------------------------------------------
# 3. Expected active root count validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expected_active_root_count_validation() -> None:
    now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
    service = ProviderTrustRootGovernanceService(MagicMock())

    # Negative count rejected
    with pytest.raises(ProviderTrustRootGovernanceError) as exc:
        await service.grant_root(
            operator_actor_id="op1",
            approver_actor_id="appr1",
            target_provider_id=uuid4(),
            valid_until=now + timedelta(days=30),
            expected_active_root_count=-1,
            governance_reference="REF-1",
            idempotency_key=str(uuid4()),
            now=now,
        )
    assert exc.value.code == "INVALID_REQUEST"

    with pytest.raises(ProviderTrustRootGovernanceError) as exc:
        await service.revoke_root(
            operator_actor_id="op1",
            approver_actor_id="appr1",
            grant_id=uuid4(),
            revocation_reason_code=RootRevocationReasonCode.ACCESS_REMOVED,
            expected_active_root_count=-1,
            governance_reference="REF-1",
            idempotency_key=str(uuid4()),
            now=now,
        )
    assert exc.value.code == "INVALID_REQUEST"


# ---------------------------------------------------------------------------
# 4. Revocation reason vocabulary
# ---------------------------------------------------------------------------


def test_revocation_reason_vocabulary() -> None:
    # Closed operator reasons
    expected_operator_reasons = {
        RootRevocationReasonCode.ACCESS_REMOVED,
        RootRevocationReasonCode.SECURITY_RESPONSE,
        RootRevocationReasonCode.GOVERNANCE_CHANGE,
        RootRevocationReasonCode.ROOT_ROTATION,
        RootRevocationReasonCode.COMPROMISE_RESPONSE,
    }
    assert OPERATOR_REVOCATION_REASONS == expected_operator_reasons

    # EXPIRED_SUPERSEDED exists but is NOT an operator reason
    assert (
        RootRevocationReasonCode.EXPIRED_SUPERSEDED not in OPERATOR_REVOCATION_REASONS
    )


@pytest.mark.asyncio
async def test_revocation_reason_validation() -> None:
    now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
    service = ProviderTrustRootGovernanceService(MagicMock())

    # Arbitrary free text reason rejected
    with pytest.raises(ProviderTrustRootGovernanceError) as exc:
        await service.revoke_root(
            operator_actor_id="op1",
            approver_actor_id="appr1",
            grant_id=uuid4(),
            revocation_reason_code="RANDOM_REASON",  # type: ignore
            expected_active_root_count=1,
            governance_reference="REF-1",
            idempotency_key=str(uuid4()),
            now=now,
        )
    assert exc.value.code == "INVALID_REQUEST"

    # EXPIRED_SUPERSEDED passed by operator rejected
    with pytest.raises(ProviderTrustRootGovernanceError) as exc:
        await service.revoke_root(
            operator_actor_id="op1",
            approver_actor_id="appr1",
            grant_id=uuid4(),
            revocation_reason_code=RootRevocationReasonCode.EXPIRED_SUPERSEDED,
            expected_active_root_count=1,
            governance_reference="REF-1",
            idempotency_key=str(uuid4()),
            now=now,
        )
    assert exc.value.code == "INVALID_REQUEST"


# ---------------------------------------------------------------------------
# 5. CLI parser tests: guards and flags
# ---------------------------------------------------------------------------


def test_cli_parser_guards() -> None:
    parser = _build_parser()

    # grant-root requires confirmation and expected arguments
    grant_args = parser.parse_args(
        [
            "grant-root",
            "--expected-database-name",
            "nexa_test_db",
            "--apply",
            "--operator-actor-id",
            "op1",
            "--approver-actor-id",
            "appr1",
            "--governance-reference",
            "CAB-001",
            "--idempotency-key",
            str(uuid4()),
            "--expected-active-root-count",
            "0",
            "--target-provider-id",
            "11111111-1111-1111-1111-111111111111",
            "--confirm-target-provider-id",
            "11111111-1111-1111-1111-111111111111",
            "--valid-until",
            "2026-10-01T00:00:00Z",
        ]
    )
    assert grant_args.subcommand == "grant-root"
    assert grant_args.apply is True
    assert grant_args.target_provider_id == grant_args.confirm_target_provider_id

    # revoke-root requires confirmation and expected arguments
    revoke_args = parser.parse_args(
        [
            "revoke-root",
            "--expected-database-name",
            "nexa_test_db",
            "--apply",
            "--operator-actor-id",
            "op1",
            "--approver-actor-id",
            "appr1",
            "--governance-reference",
            "CAB-002",
            "--idempotency-key",
            str(uuid4()),
            "--expected-active-root-count",
            "1",
            "--grant-id",
            "22222222-2222-2222-2222-222222222222",
            "--confirm-grant-id",
            "22222222-2222-2222-2222-222222222222",
            "--reason",
            "ACCESS_REMOVED",
            "--acknowledge-zero-active-roots",
        ]
    )
    assert revoke_args.subcommand == "revoke-root"
    assert revoke_args.apply is True
    assert revoke_args.acknowledge_zero_active_roots is True


@pytest.mark.asyncio
async def test_cli_missing_apply_guard() -> None:
    # Invocation without --apply returns 1 and EXPLICIT_APPLY_REQUIRED
    with patch("sys.stderr.write") as mock_err:
        exit_code = await run_governance(
            [
                "grant-root",
                "--expected-database-name",
                "nexa_test_db",
                "--operator-actor-id",
                "op1",
                "--approver-actor-id",
                "appr1",
                "--governance-reference",
                "CAB-001",
                "--idempotency-key",
                str(uuid4()),
                "--expected-active-root-count",
                "0",
                "--target-provider-id",
                "11111111-1111-1111-1111-111111111111",
                "--confirm-target-provider-id",
                "11111111-1111-1111-1111-111111111111",
                "--valid-until",
                "2026-10-01T00:00:00Z",
            ]
        )
    assert exit_code == 1
    mock_err.assert_called()
    assert "EXPLICIT_APPLY_REQUIRED" in mock_err.call_args[0][0]


@pytest.mark.asyncio
async def test_cli_missing_env_db_url() -> None:
    # Invocation with --apply but missing NEXA_TRUST_ROOT_DATABASE_URL returns 1 and INVALID_REQUEST
    with (
        patch.dict("os.environ", {}, clear=True),
        patch("sys.stderr.write") as mock_err,
    ):
        exit_code = await run_governance(
            [
                "grant-root",
                "--expected-database-name",
                "nexa_test_db",
                "--apply",
                "--operator-actor-id",
                "op1",
                "--approver-actor-id",
                "appr1",
                "--governance-reference",
                "CAB-001",
                "--idempotency-key",
                str(uuid4()),
                "--expected-active-root-count",
                "0",
                "--target-provider-id",
                "11111111-1111-1111-1111-111111111111",
                "--confirm-target-provider-id",
                "11111111-1111-1111-1111-111111111111",
                "--valid-until",
                "2026-10-01T00:00:00Z",
            ]
        )
    assert exit_code == 1
    mock_err.assert_called()
    assert "NEXA_TRUST_ROOT_DATABASE_URL" in mock_err.call_args[0][0]


# ---------------------------------------------------------------------------
# 6. Preflight guards: database name and schema revision
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_guards_db_name_mismatch() -> None:
    session = AsyncMock()
    session.in_transaction = MagicMock(return_value=False)
    # Mock current_database() to return 'actual_db'
    mock_res = MagicMock()
    mock_res.scalar.return_value = "actual_db"
    session.execute.return_value = mock_res

    with pytest.raises(ProviderTrustRootGovernanceError) as exc:
        await _preflight_guards(session, "expected_db")
    assert exc.value.code == "DATABASE_NAME_MISMATCH"


@pytest.mark.asyncio
async def test_preflight_guards_schema_mismatch() -> None:
    session = AsyncMock()
    session.in_transaction = MagicMock(return_value=False)
    # Mock current_database() to match
    db_name_res = MagicMock()
    db_name_res.scalar.return_value = "expected_db"

    # Mock alembic_version.version_num to differ from repo head
    alembic_res = MagicMock()
    alembic_res.scalar_one_or_none.return_value = "old_revision"

    session.execute.side_effect = [db_name_res, alembic_res]

    with (
        patch(
            "scripts.governance_trust_root._derive_repository_heads",
            return_value=("current_repo_head",),
        ),
        pytest.raises(ProviderTrustRootGovernanceError) as exc,
    ):
        await _preflight_guards(session, "expected_db")
    assert exc.value.code == "SCHEMA_REVISION_MISMATCH"


@pytest.mark.asyncio
async def test_preflight_guards_multiple_heads_mismatch() -> None:
    session = AsyncMock()
    session.in_transaction = MagicMock(return_value=False)
    db_name_res = MagicMock()
    db_name_res.scalar.return_value = "expected_db"
    session.execute.return_value = db_name_res

    with (
        patch(
            "scripts.governance_trust_root._derive_repository_heads",
            return_value=("head_1", "head_2"),
        ),
        pytest.raises(ProviderTrustRootGovernanceError) as exc,
    ):
        await _preflight_guards(session, "expected_db")
    assert exc.value.code == "SCHEMA_REVISION_MISMATCH"


# ---------------------------------------------------------------------------
# 7. Architecture boundaries and no-bypass invariants
# ---------------------------------------------------------------------------


def test_no_forbidden_imports_in_root_governance() -> None:
    """Ensure root governance modules do not import FastAPI, Redis, or provider session primitives."""
    forbidden_modules = {
        "fastapi",
        "redis",
        "starlette",
        "app.api.v2.provider_trust_routes",
        "app.api.v2.provider_trust_permission_routes",
        "app.core.redis",
    }

    files_to_check = [
        ROOT / "app" / "services" / "provider_trust_root_governance.py",
        ROOT / "scripts" / "governance_trust_root.py",
    ]

    for file_path in files_to_check:
        content = file_path.read_text(encoding="utf-8")
        parsed = ast.parse(content, filename=str(file_path))
        for node in ast.walk(parsed):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert (
                        alias.name not in forbidden_modules
                    ), f"{file_path} imports forbidden {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    assert (
                        node.module not in forbidden_modules
                    ), f"{file_path} imports from forbidden {node.module}"


def test_no_root_bypass_flags_in_subordinate_layers() -> None:
    """Verify that Phase 4B, 4C, 4D, and 4E contain no allow_root / bypass flags."""
    forbidden_tokens = [
        "allow_root",
        "bootstrap",
        "system_actor",
        "super_admin",
        "skip_authorization",
    ]

    files_to_check = [
        ROOT / "app" / "services" / "provider_trust_permission_policy.py",
        ROOT / "app" / "services" / "provider_trust_permission_application.py",
        ROOT / "app" / "api" / "v2" / "provider_trust_permission_routes.py",
    ]

    for file_path in files_to_check:
        content = file_path.read_text(encoding="utf-8")
        for token in forbidden_tokens:
            # Check function signatures and arguments
            assert (
                f"{token}=" not in content
            ), f"{file_path} contains forbidden flag {token}="
            assert (
                f"{token}:" not in content
            ), f"{file_path} contains forbidden param {token}:"


def test_fastapi_provider_trust_route_surface_remains_26() -> None:
    """Verify FastAPI public surface under /api/v2/provider-trust remains exactly 26 POST routes."""
    trust_routes = [
        route
        for route in app.routes
        if hasattr(route, "path") and route.path.startswith("/api/v2/provider-trust")
    ]
    assert len(trust_routes) == 26

    # Verify all 26 are POST routes
    for route in trust_routes:
        methods = getattr(route, "methods", set())
        assert methods == {
            "POST"
        }, f"Route {route.path} has non-POST methods: {methods}"


def test_audit_event_vocabulary_frozen_in_root_governance() -> None:
    """Ensure root governance uses only registered permission events and no root event literals."""
    from app.observability.provider_trust_events import ProviderTrustAuditEvent

    # 1. Verify app/observability/provider_trust_events.py contains no root-specific events
    all_event_names = {e.name for e in ProviderTrustAuditEvent}
    assert "PROVIDER_TRUST_ROOT_GRANTED" not in all_event_names
    assert "PROVIDER_TRUST_ROOT_REVOKED" not in all_event_names

    # 2. Verify root governance implementation files contain no raw string or literal mentions
    forbidden_literals = [
        "PROVIDER_TRUST_ROOT_GRANTED",
        "PROVIDER_TRUST_ROOT_REVOKED",
    ]
    files_to_check = [
        ROOT / "app" / "services" / "provider_trust_root_governance.py",
        ROOT / "scripts" / "governance_trust_root.py",
    ]
    for file_path in files_to_check:
        content = file_path.read_text(encoding="utf-8")
        # Neither file may contain forbidden root literals
        for literal in forbidden_literals:
            assert (
                literal not in content
            ), f"{file_path} contains forbidden event literal {literal}"

    # Must reference the standard permission events in service
    service_content = (
        ROOT / "app" / "services" / "provider_trust_root_governance.py"
    ).read_text(encoding="utf-8")
    assert "PROVIDER_TRUST_PERMISSION_GRANTED" in service_content
    assert "PROVIDER_TRUST_PERMISSION_REVOKED" in service_content
