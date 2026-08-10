from __future__ import annotations

import importlib.util
from pathlib import Path

from sqlalchemy import CheckConstraint, UniqueConstraint

from app.models.base import Base
from app.models.identity_review import (  # noqa: F401 - registers metadata
    IdentityReviewCaseRecord,
    IdentityReviewCaseRouteRecord,
    IdentityReviewDispositionRecord,
    IdentityReviewOperationRecord,
)


def test_identity_review_revision_and_single_expected_parent():
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/20260810_identity_review.py"
    )
    spec = importlib.util.spec_from_file_location("identity_review_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "20260810_identity_review"
    assert migration.down_revision == "20260806_eligibility_reason"
    assert callable(migration.upgrade)
    assert callable(migration.downgrade)


def test_four_dedicated_tables_are_registered_with_closed_constraints():
    names = {
        "identity_review_cases",
        "identity_review_case_routes",
        "identity_review_dispositions",
        "identity_review_operations",
    }
    assert names <= set(Base.metadata.tables)
    cases = Base.metadata.tables["identity_review_cases"]
    routes = Base.metadata.tables["identity_review_case_routes"]
    dispositions = Base.metadata.tables["identity_review_dispositions"]
    operations = Base.metadata.tables["identity_review_operations"]

    case_checks = {
        constraint.name
        for constraint in cases.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert {
        "ck_identity_review_cases_status",
        "ck_identity_review_cases_reason_codes",
        "ck_identity_review_cases_assignment_state",
        "ck_identity_review_cases_reviewer_separation",
    } <= case_checks
    assert any(
        isinstance(constraint, UniqueConstraint)
        and tuple(column.name for column in constraint.columns) == ("job_id",)
        for constraint in cases.constraints
    )
    assert {column.name for column in routes.columns} >= {
        "case_id",
        "routing_id",
        "decision_id",
        "job_id",
        "patient_id",
        "tenant_id",
        "source_document_id",
    }
    assert any(
        isinstance(constraint, UniqueConstraint)
        and tuple(column.name for column in constraint.columns) == ("case_id",)
        for constraint in dispositions.constraints
    )
    disposition_role_checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in dispositions.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert disposition_role_checks == {
        "ck_identity_review_dispositions_reviewer_role": "reviewer_role = 'identity_reviewer'",
        "ck_identity_review_dispositions_outcome": disposition_role_checks[
            "ck_identity_review_dispositions_outcome"
        ],
        "ck_identity_review_dispositions_reason_codes": disposition_role_checks[
            "ck_identity_review_dispositions_reason_codes"
        ],
        "ck_identity_review_dispositions_outcome_reasons": disposition_role_checks[
            "ck_identity_review_dispositions_outcome_reasons"
        ],
        "ck_identity_review_dispositions_prior_version_positive": disposition_role_checks[
            "ck_identity_review_dispositions_prior_version_positive"
        ],
        "ck_identity_review_dispositions_operation_hash_length": disposition_role_checks[
            "ck_identity_review_dispositions_operation_hash_length"
        ],
    }
    assert {
        constraint.name
        for constraint in operations.constraints
        if isinstance(constraint, CheckConstraint)
    } >= {
        "ck_identity_review_operations_type",
        "ck_identity_review_operations_versions",
        "ck_identity_review_operations_hash_length",
    }


def test_all_authoritative_foreign_keys_are_restrictive():
    for table_name in {
        "identity_review_cases",
        "identity_review_case_routes",
        "identity_review_dispositions",
        "identity_review_operations",
    }:
        for foreign_key in Base.metadata.tables[table_name].foreign_keys:
            assert foreign_key.ondelete == "RESTRICT"


def test_migration_defines_only_phase_one_tables_and_reverse_drop_order():
    source = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/20260810_identity_review.py"
    ).read_text(encoding="utf-8")
    disposition_source = source.split(
        '        "identity_review_dispositions",', 1
    )[1].split("    op.create_table(", 1)[0]
    assert '"reviewer_role = \'identity_reviewer\'"' in disposition_source
    assert (
        'name="ck_identity_review_dispositions_reviewer_role"'
        in disposition_source
    )
    assert source.count("op.create_table(") == 4
    assert source.count("op.drop_table(") == 4
    assert (
        source.index('op.drop_table("identity_review_operations")')
        < source.index('op.drop_table("identity_review_dispositions")')
        < source.index('op.drop_table("identity_review_case_routes")')
        < source.index('op.drop_table("identity_review_cases")')
    )


def test_generic_document_policy_is_not_extended_with_identity_operations():
    source = (
        Path(__file__).resolve().parents[1]
        / "app/security/document_processing_policy.py"
    ).read_text(encoding="utf-8")
    assert "IDENTITY_REVIEW" not in source
    assert "identity_reviewer" not in source
