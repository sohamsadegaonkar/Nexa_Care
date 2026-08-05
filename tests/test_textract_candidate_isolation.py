from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v2.pipeline_routes import _assert_candidate_authorization_binding
from app.models.pipeline import ExtractionCandidateRecord

ROOT = Path(__file__).resolve().parents[1]


def bound_objects():
    job = SimpleNamespace(
        id=uuid4(),
        document_id=uuid4(),
        patient_id=uuid4(),
        tenant_id=uuid4(),
    )
    provider = SimpleNamespace(actor_uid="provider-a")
    candidate = SimpleNamespace(
        job_id=job.id,
        source_document_id=job.document_id,
        patient_id=job.patient_id,
        tenant_id=job.tenant_id,
        authorization_provider_id=provider.actor_uid,
    )
    return candidate, job, provider


def test_candidate_binding_accepts_only_exact_job_patient_tenant_and_provider():
    candidate, job, provider = bound_objects()
    _assert_candidate_authorization_binding(
        candidate=candidate, job=job, provider=provider
    )


@pytest.mark.parametrize(
    ("attribute", "replacement"),
    [
        ("job_id", uuid4()),
        ("source_document_id", uuid4()),
        ("patient_id", uuid4()),
        ("tenant_id", uuid4()),
        ("authorization_provider_id", "provider-b"),
    ],
)
def test_candidate_binding_fails_closed_on_cross_resource_access(
    attribute, replacement
):
    candidate, job, provider = bound_objects()
    setattr(candidate, attribute, replacement)
    with pytest.raises(HTTPException) as exc:
        _assert_candidate_authorization_binding(
            candidate=candidate, job=job, provider=provider
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == {"error_code": "CANDIDATE_AUTHORIZATION_MISMATCH"}


def test_candidate_staging_has_no_plaintext_value_or_source_columns():
    columns = {column.name for column in ExtractionCandidateRecord.__table__.columns}
    assert "raw_value" not in columns
    assert "source_text" not in columns
    assert {"encrypted_raw_value", "encrypted_source_text"}.issubset(columns)


def test_candidate_migration_enforces_binding_encryption_and_safe_lanes():
    code = (ROOT / "alembic/versions/20260801_textract_candidates.py").read_text(
        encoding="utf-8"
    )
    for required in [
        "encrypted_raw_value",
        "encrypted_source_text",
        "authorization_provider_id",
        "source_document_id",
        "patient_id",
        "tenant_id",
        "ck_extraction_candidates_safe_lane",
        'ondelete="CASCADE"',
    ]:
        assert required in code


def test_candidate_eligibility_migration_is_chained_and_non_destructive():
    code = (ROOT / "alembic/versions/20260806_candidate_eligibility.py").read_text(
        encoding="utf-8"
    )
    assert 'down_revision = "20260801_textract_candidates"' in code
    for required in (
        '"routing_eligible"',
        '"eligibility_reason_code"',
        '"eligibility_policy_version"',
        "ck_extraction_candidates_eligibility",
        "ix_extraction_candidates_job_routing_eligible",
        "UPDATE extraction_candidates SET routing_eligible = TRUE",
        "UPDATE extraction_candidates SET eligibility_policy_version = 'v1'",
        "def downgrade()",
    ):
        assert required in code


def test_candidate_model_separates_eligibility_from_lane():
    columns = {column.name for column in ExtractionCandidateRecord.__table__.columns}
    assert {
        "routing_eligible",
        "eligibility_reason_code",
        "eligibility_policy_version",
    }.issubset(columns)
    assert "lane" in columns
    constraints = {
        constraint.name
        for constraint in ExtractionCandidateRecord.__table__.constraints
    }
    assert "ck_extraction_candidates_eligibility" in constraints
