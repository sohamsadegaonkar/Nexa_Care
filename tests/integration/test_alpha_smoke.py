"""Cross-component pilot seam smoke tests without fabricated clinical flows."""

from pathlib import Path

import pytest

from app.main import app

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.integration
def test_signed_approval_to_durable_claim_seam_is_registered():
    routes = {
        (method, route.path)
        for route in app.routes
        for method in getattr(route, "methods", set())
    }
    assert ("POST", "/api/v2/consent/approve-signed") in routes
    assert ("POST", "/api/v2/consent/{request_id}/claim-access") in routes
    source = (ROOT / "app/api/v2/consent_routes.py").read_text(encoding="utf-8")
    assert "ConsentGrantLog(" in source
    assert "hospital_id=provider.hospital_id" in source


@pytest.mark.integration
def test_document_upload_to_review_only_commit_seam():
    routes = (ROOT / "app/api/v2/pipeline_routes.py").read_text(encoding="utf-8")
    orchestrator = (ROOT / "app/services/pipeline_orchestrator.py").read_text(
        encoding="utf-8"
    )
    ingestion = (ROOT / "app/services/record_ingestion.py").read_text(encoding="utf-8")
    assert "get_document_storage" in routes
    assert "process_extraction_job" in routes
    assert 'status="needs_review"' in orchestrator
    assert 'not in {"approved", "edited"}' in ingestion
    assert "CLIENT_SUPPLIED_COMMIT_FIELDS_FORBIDDEN" in routes
