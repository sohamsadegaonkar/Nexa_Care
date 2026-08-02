from fastapi.routing import APIRoute

from app.api.v2.pipeline_routes import ExtractionJobStatusResponse, router


def _status_response() -> ExtractionJobStatusResponse:
    return ExtractionJobStatusResponse(
        job_id="00000000-0000-0000-0000-000000000001",
        patient_id="00000000-0000-0000-0000-000000000002",
        status="source_only",
        document_type="lab_report",
        provider="aws_textract",
        provider_version="queries-v1",
        document_confidence=None,
        routing_lane="SOURCE_ONLY",
        candidate_count=0,
        identity_validation="passed",
        created_at="2026-08-02T00:00:00+00:00",
    )


def test_job_status_response_serializes_safe_empty_array_defaults() -> None:
    first = _status_response()
    second = _status_response()

    assert first.model_dump()["routing_reasons"] == []
    assert first.model_dump()["extracted_fields"] == []
    assert first.model_dump()["candidates"] == []
    assert first.routing_reasons is not second.routing_reasons
    assert first.extracted_fields is not second.extracted_fields


def test_job_status_route_enforces_the_typed_response_model() -> None:
    route = next(
        route
        for route in router.routes
        if isinstance(route, APIRoute)
        and route.path == "/api/v2/pipeline/jobs/{job_id}"
        and "GET" in route.methods
    )

    assert route.response_model is ExtractionJobStatusResponse
