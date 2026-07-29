"""Source-level regression guards for pipeline authorization boundaries."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE = (ROOT / "app/api/v2/pipeline_routes.py").read_text(encoding="utf-8")


def test_upload_uses_form_patient_and_upload_file():
    assert "patient_id: uuid.UUID = Form(...)" in CODE
    assert "file: UploadFile = File(...)" in CODE


def test_upload_enforces_bounded_size_and_content_hash():
    assert "MAX_UPLOAD_BYTES" in CODE
    assert "content_hash" in CODE


def test_upload_records_tenant_uploader_purpose_and_consent():
    for field in ("tenant_id", "uploader_id", "upload_purpose", "consent_session_id"):
        assert field in CODE


def test_job_and_field_identifiers_are_strict_uuid():
    assert '"error_code": "INVALID_UUID"' in CODE


def test_review_uses_row_lock_role_tenant_and_version():
    assert ".with_for_update()" in CODE
    assert "REVIEW_ROLE_REQUIRED" in CODE
    assert "CROSS_TENANT_JOB_ACCESS" in CODE
    assert "STALE_REVIEW_VERSION" in CODE


def test_commit_rejects_unresolved_fields():
    assert "Review incomplete: job contains unresolved fields" in CODE


def test_commit_loads_approved_fields_from_database():
    assert 'ExtractedFieldRecord.status.in_(["approved", "edited"])' in CODE
    assert "CLIENT_SUPPLIED_COMMIT_FIELDS_FORBIDDEN" in CODE


def test_commit_is_idempotent_and_tenant_scoped():
    assert "JOB_ALREADY_COMMITTED" in CODE
    assert "CROSS_TENANT_JOB_ACCESS" in CODE


def test_no_fabricated_clinical_default_survives_commit_route():
    assert "120/80" not in CODE
    assert "field_name or" not in CODE


def test_commit_audits_success():
    assert 'event_type="JOB_COMMITTED"' in CODE
    assert "enqueue_audit_event(" in CODE
    assert CODE.index("enqueue_audit_event(") < CODE.rindex("await db.commit()")
