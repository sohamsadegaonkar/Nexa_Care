"""Server-derived consent and tenant-bound pipeline contracts."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE = (ROOT / "app/api/v2/pipeline_routes.py").read_text(encoding="utf-8")


def test_job_status_loads_job_before_consent_validation():
    start = CODE.index("async def get_extraction_job")
    body = CODE[start : CODE.index("# ── Review queue", start)]
    assert body.index("select(ExtractionJob)") < body.index(
        "validate_consent_for_patient"
    )
    assert "patient_id=str(job.patient_id)" in body


def test_job_status_does_not_read_patient_header_or_query_parameter():
    body = CODE[
        CODE.index("async def get_extraction_job") : CODE.index("# ── Review queue")
    ]
    assert "X-Patient-Id" not in body
    assert "query_params" not in body


def test_field_review_derives_patient_from_parent_job():
    body = CODE[
        CODE.index("async def review_extracted_field") : CODE.index(
            '@router.post("/fields/{field_id}/approve"'
        )
    ]
    assert "field.job_id" in body
    assert "server_patient_id = str(job.patient_id)" in body


def test_nonexistent_field_returns_not_found_without_client_identity_fallback():
    assert (
        'raise HTTPException(status_code=404, detail="Extracted field not found")'
        in CODE
    )


def test_commit_compares_payload_patient_to_locked_job_patient():
    assert "str(_parse_uuid(payload.patient_id)) != str(job.patient_id)" in CODE


def test_commit_ingestion_uses_server_patient_id():
    assert "patient_id=server_pid" in CODE


def test_upload_validates_consent_for_form_patient():
    body = CODE[
        CODE.index("async def upload_pipeline_document") : CODE.index(
            '@router.get("/jobs/{job_id}"'
        )
    ]
    assert "patient_id=str(patient_id)" in body
    assert "validate_consent_for_patient" in body


def test_entity_routes_enforce_provider_hospital_tenant():
    assert CODE.count("CROSS_TENANT_JOB_ACCESS") >= 3
