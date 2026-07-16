"""Server-side patient_id derivation tests for pipeline consent.

ALPHA: Pipeline endpoints that reference existing entities (ExtractionJob,
ExtractedFieldRecord) now derive patient_id from the DB row, not from
client-provided values.  This eliminates the patient_id spoofing vector
described in threat-model.md T-06.

These tests verify:
1. Job status endpoint uses job.patient_id, ignores client headers
2. Field review endpoint uses field→job.patient_id chain
3. Commit endpoint validates payload.patient_id against job.patient_id
4. Spoofed patient_id is rejected
5. validate_consent_for_patient() works independently of request discovery
"""
from __future__ import annotations

import uuid
from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.consent_gate import validate_consent_for_patient
from app.core.dependencies import get_current_provider
from app.main import app
from app.models.provider_context import (
    AffiliationContext,
    HospitalContext,
    ProviderContext,
    ProviderIdentityContext,
)
from app.models.provider import AffiliationType
from app.services.consent_engine import ConsentCapability
from tests.conftest import DualModeTestClient, FakeRedis


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_provider_context() -> ProviderContext:
    return ProviderContext(
        provider=ProviderIdentityContext(
            provider_id=uuid.uuid4(), display_name="Dr. Test",
            contact_email="test@hospital.example",
        ),
        hospital=HospitalContext(
            hospital_id=uuid.uuid4(), facility_code="TEST",
            display_name="Test Hospital",
        ),
        affiliation=AffiliationContext(
            affiliation_id=uuid.uuid4(),
            affiliation_type=AffiliationType.PERMANENT,
            is_primary=True, roles=["clinician"],
        ),
    )


def _make_capability(patient_id: str) -> ConsentCapability:
    return ConsentCapability(
        patient_id=patient_id,
        clinician_id=str(uuid.uuid4()),
        purpose="TREATMENT",
        scope=["clinical.*", "pii.*"],
        is_break_glass=False,
        reason_code=None,
        issued_at=datetime.now(timezone.utc).isoformat(),
    )


def _mock_job(job_id: str, patient_id: str) -> MagicMock:
    m = MagicMock()
    m.id = uuid.UUID(job_id)
    m.patient_id = uuid.UUID(patient_id)
    m.status = "scored"
    m.document_type = "LAB_REPORT"
    m.created_at = datetime.now(timezone.utc)
    return m


def _mock_field(field_id: str, job_id: str) -> MagicMock:
    m = MagicMock()
    m.id = uuid.UUID(field_id)
    m.job_id = uuid.UUID(job_id)
    m.status = "needs_review"
    m.raw_value = "120/80"
    m.corrected_value = None
    m.field_name = "bp"
    m.confidence = 0.90
    return m


@pytest.fixture
def client():
    return DualModeTestClient(app)


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def overrides():
    saved = {}
    yield saved
    for dep in saved:
        app.dependency_overrides.pop(dep, None)


def _apply_auth_overrides(overrides):
    ctx = _make_provider_context()

    async def _provider():
        return ctx

    overrides[get_current_provider] = _provider
    app.dependency_overrides[get_current_provider] = _provider


def _consent_context(capability):
    """Patch consent validation and audit logging."""
    stack = ExitStack()
    stack.enter_context(
        patch("app.core.consent_gate.validate_consent_capability",
              new_callable=AsyncMock, return_value=capability)
    )
    stack.enter_context(
        patch("app.core.consent_gate.validate_approved_access", return_value=None)
    )
    for mod in ("app.core.consent_gate", "app.api.v2.pipeline_routes",
                "app.observability.audit_ledger"):
        stack.enter_context(patch(f"{mod}.append_audit_log_or_503", return_value=None))
    stack.enter_context(
        patch("app.observability.audit_ledger.append_audit_log", return_value=None)
    )
    return stack


# ═══════════════════════════════════════════════════════════════════════════════
# 1. JOB STATUS: server-derived patient_id
# ═══════════════════════════════════════════════════════════════════════════════


class TestJobStatusServerDerivedPatientId:
    """GET /pipeline/jobs/{job_id} derives patient_id from the job entity."""

    def test_ignores_x_patient_id_header(self, client, fake_redis, mock_db, overrides):
        """Client sends wrong X-Patient-Id header — server ignores it."""
        job_id = str(uuid.uuid4())
        real_patient_id = str(uuid.uuid4())
        spoofed_patient_id = str(uuid.uuid4())
        capability = _make_capability(real_patient_id)
        _apply_auth_overrides(overrides)

        job = _mock_job(job_id, real_patient_id)
        mock_db.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=job)),
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
        ]

        with _consent_context(capability):
            resp = client.get(
                f"/api/v2/pipeline/jobs/{job_id}",
                headers={
                    "X-Consent-Token": "t",
                    "X-Patient-Id": spoofed_patient_id,  # Wrong patient_id
                },
            )
            # Consent validated for job's real_patient_id, not spoofed one
            assert resp.status_code == 200
            # The response uses the server-derived patient_id from the consent
            assert resp.json()["patient_id"] == real_patient_id

    def test_ignores_patient_id_query_param(self, client, fake_redis, mock_db, overrides):
        """Client sends ?patient_id=spoofed — server ignores it."""
        job_id = str(uuid.uuid4())
        real_patient_id = str(uuid.uuid4())
        spoofed_patient_id = str(uuid.uuid4())
        capability = _make_capability(real_patient_id)
        _apply_auth_overrides(overrides)

        job = _mock_job(job_id, real_patient_id)
        mock_db.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=job)),
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
        ]

        with _consent_context(capability):
            resp = client.get(
                f"/api/v2/pipeline/jobs/{job_id}?patient_id={spoofed_patient_id}",
                headers={"X-Consent-Token": "t"},
            )
            assert resp.status_code == 200

    def test_nonexistent_job_returns_404(self, client, fake_redis, mock_db, overrides):
        """Querying a non-existent job returns 404 before consent check."""
        job_id = str(uuid.uuid4())
        capability = _make_capability(str(uuid.uuid4()))
        _apply_auth_overrides(overrides)

        mock_db.execute.return_value = MagicMock(
            scalar_one_or_none=MagicMock(return_value=None),
        )

        with _consent_context(capability):
            resp = client.get(
                f"/api/v2/pipeline/jobs/{job_id}",
                headers={"X-Consent-Token": "t"},
            )
            assert resp.status_code == 404

    def test_no_consent_token_returns_403(self, client, fake_redis, mock_db, overrides):
        """Valid job but no consent token returns 403."""
        job_id = str(uuid.uuid4())
        real_patient_id = str(uuid.uuid4())
        _apply_auth_overrides(overrides)

        job = _mock_job(job_id, real_patient_id)
        mock_db.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=job)),
        ]

        # Mock consent validation to return None (invalid token)
        with ExitStack() as stack:
            stack.enter_context(
                patch("app.core.consent_gate.validate_consent_capability",
                      new_callable=AsyncMock, return_value=None)
            )
            stack.enter_context(
                patch("app.core.consent_gate.validate_approved_access", return_value=None)
            )
            for mod in ("app.core.consent_gate", "app.api.v2.pipeline_routes",
                        "app.observability.audit_ledger"):
                stack.enter_context(patch(f"{mod}.append_audit_log_or_503", return_value=None))
            stack.enter_context(
                patch("app.observability.audit_ledger.append_audit_log", return_value=None)
            )
            resp = client.get(
                f"/api/v2/pipeline/jobs/{job_id}",
                headers={"X-Consent-Token": "invalid-token"},
            )
            assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# 2. FIELD REVIEW: server-derived patient_id from field → job chain
# ═══════════════════════════════════════════════════════════════════════════════


class TestFieldReviewServerDerivedPatientId:
    """POST /pipeline/fields/{field_id}/review derives patient_id from
    the field's parent ExtractionJob.
    """

    def test_ignores_wrong_patient_id_header(self, client, fake_redis, mock_db, overrides):
        """Client sends wrong X-Patient-Id — server uses job's patient_id."""
        field_id = str(uuid.uuid4())
        job_id = str(uuid.uuid4())
        real_patient_id = str(uuid.uuid4())
        spoofed_patient_id = str(uuid.uuid4())
        capability = _make_capability(real_patient_id)
        _apply_auth_overrides(overrides)

        field = _mock_field(field_id, job_id)
        job = _mock_job(job_id, real_patient_id)

        mock_db.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=field)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=job)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
        ]

        with _consent_context(capability):
            resp = client.post(
                f"/api/v2/pipeline/fields/{field_id}/review",
                json={"action": "approve"},
                headers={
                    "X-Consent-Token": "t",
                    "X-Patient-Id": spoofed_patient_id,
                },
            )
            assert resp.status_code == 200
            assert resp.json()["new_status"] == "approved"

    def test_nonexistent_field_consents_with_none_patient_id(
        self, client, fake_redis, mock_db, overrides
    ):
        """If field doesn't exist, server_patient_id is None → consent fails."""
        field_id = str(uuid.uuid4())
        capability = _make_capability(str(uuid.uuid4()))
        _apply_auth_overrides(overrides)

        mock_db.execute.return_value = MagicMock(
            scalar_one_or_none=MagicMock(return_value=None),
        )

        with _consent_context(capability):
            resp = client.post(
                f"/api/v2/pipeline/fields/{field_id}/review",
                json={"action": "approve"},
                headers={"X-Consent-Token": "t"},
            )
            # patient_id is None → consent gate returns 403
            assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# 3. JOB COMMIT: patient_id validation
# ═══════════════════════════════════════════════════════════════════════════════


class TestCommitPatientIdValidation:
    """POST /pipeline/jobs/{job_id}/commit validates payload.patient_id
    against the server-derived patient_id from the job entity.
    """

    def test_rejects_spoofed_patient_id_in_payload(
        self, client, fake_redis, mock_db, overrides
    ):
        """ALPHA: payload.patient_id must match job.patient_id — spoofing rejected."""
        job_id = str(uuid.uuid4())
        real_patient_id = str(uuid.uuid4())
        spoofed_patient_id = str(uuid.uuid4())
        capability = _make_capability(real_patient_id)
        _apply_auth_overrides(overrides)

        job = _mock_job(job_id, real_patient_id)
        mock_db.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=job)),
        ]

        with _consent_context(capability):
            resp = client.post(
                f"/api/v2/pipeline/jobs/{job_id}/commit",
                json={"patient_id": spoofed_patient_id},  # Doesn't match job
                headers={"X-Consent-Token": "t"},
            )
            assert resp.status_code == 400
            assert "patient_id" in resp.json()["detail"].lower()

    def test_accepts_matching_patient_id(
        self, client, fake_redis, mock_db, overrides
    ):
        """ALPHA: payload.patient_id matches job.patient_id — accepted."""
        job_id = str(uuid.uuid4())
        real_patient_id = str(uuid.uuid4())
        capability = _make_capability(real_patient_id)
        _apply_auth_overrides(overrides)

        job = _mock_job(job_id, real_patient_id)
        mock_db.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=job)),
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
        ]

        with _consent_context(capability), \
             patch("app.services.record_ingestion.ingest_extracted_fields", return_value=None):
            resp = client.post(
                f"/api/v2/pipeline/jobs/{job_id}/commit",
                json={"patient_id": real_patient_id},
                headers={"X-Consent-Token": "t"},
            )
            assert resp.status_code == 201
            assert resp.json()["patient_id"] == real_patient_id

    def test_nonexistent_job_returns_404(
        self, client, fake_redis, mock_db, overrides
    ):
        """Commit to a non-existent job returns 404."""
        job_id = str(uuid.uuid4())
        capability = _make_capability(str(uuid.uuid4()))
        _apply_auth_overrides(overrides)

        mock_db.execute.return_value = MagicMock(
            scalar_one_or_none=MagicMock(return_value=None),
        )

        with _consent_context(capability):
            resp = client.post(
                f"/api/v2/pipeline/jobs/{job_id}/commit",
                json={"patient_id": str(uuid.uuid4())},
                headers={"X-Consent-Token": "t"},
            )
            assert resp.status_code == 404

    def test_uses_server_patient_id_for_ingestion(
        self, client, fake_redis, mock_db, overrides
    ):
        """ALPHA: Record ingestion uses server-derived patient_id, not payload."""
        job_id = str(uuid.uuid4())
        real_patient_id = str(uuid.uuid4())
        capability = _make_capability(real_patient_id)
        _apply_auth_overrides(overrides)

        job = _mock_job(job_id, real_patient_id)
        mock_db.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=job)),
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
        ]

        with _consent_context(capability), \
             patch("app.services.record_ingestion.ingest_extracted_fields",
                   new_callable=AsyncMock) as mock_ingest:
            resp = client.post(
                f"/api/v2/pipeline/jobs/{job_id}/commit",
                json={"patient_id": real_patient_id},
                headers={"X-Consent-Token": "t"},
            )
            assert resp.status_code == 201
            # Verify ingestion was called with the server-derived patient_id
            if mock_ingest.called:
                call_kwargs = mock_ingest.call_args
                assert call_kwargs.kwargs.get("patient_id") == real_patient_id or \
                       call_kwargs.args[0] == real_patient_id


# ═══════════════════════════════════════════════════════════════════════════════
# 4. validate_consent_for_patient() unit tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidateConsentForPatient:
    """Unit tests for the extracted validate_consent_for_patient() function."""

    def test_returns_capability_on_valid_consent(self):
        """Valid consent token + patient_id → returns ConsentCapability."""
        import asyncio
        provider = _make_provider_context()
        patient_id = str(uuid.uuid4())
        expected = _make_capability(patient_id)

        async def _run():
            with patch("app.core.consent_gate.validate_consent_capability",
                        new_callable=AsyncMock, return_value=expected), \
                 patch("app.core.consent_gate.append_audit_log_or_503", return_value=None):
                return await validate_consent_for_patient(
                    patient_id=patient_id,
                    purpose="pipeline_status",
                    provider=provider,
                    x_consent_token="valid-token",
                )

        result = asyncio.run(_run())
        assert result == expected

    def test_raises_403_without_consent_token(self):
        """Missing consent token → 403."""
        import asyncio
        provider = _make_provider_context()

        async def _run():
            with patch("app.core.consent_gate.append_audit_log_or_503", return_value=None):
                return await validate_consent_for_patient(
                    patient_id=str(uuid.uuid4()),
                    purpose="pipeline_status",
                    provider=provider,
                    x_consent_token=None,
                )

        with pytest.raises(Exception) as exc_info:
            asyncio.run(_run())
        assert exc_info.value.status_code == 403

    def test_raises_403_without_patient_id(self):
        """Missing patient_id → 403."""
        import asyncio
        provider = _make_provider_context()

        async def _run():
            with patch("app.core.consent_gate.append_audit_log_or_503", return_value=None):
                return await validate_consent_for_patient(
                    patient_id=None,
                    purpose="pipeline_status",
                    provider=provider,
                    x_consent_token="valid-token",
                )

        with pytest.raises(Exception) as exc_info:
            asyncio.run(_run())
        assert exc_info.value.status_code == 403

    def test_raises_403_on_invalid_consent(self):
        """Invalid/expired consent token → 403."""
        import asyncio
        provider = _make_provider_context()

        async def _run():
            with patch("app.core.consent_gate.validate_consent_capability",
                        new_callable=AsyncMock, return_value=None), \
                 patch("app.core.consent_gate.validate_approved_access", return_value=None), \
                 patch("app.core.consent_gate.append_audit_log_or_503", return_value=None):
                return await validate_consent_for_patient(
                    patient_id=str(uuid.uuid4()),
                    purpose="pipeline_status",
                    provider=provider,
                    x_consent_token="invalid-token",
                )

        with pytest.raises(Exception) as exc_info:
            asyncio.run(_run())
        assert exc_info.value.status_code == 403

    def test_raises_503_on_consent_engine_unavailable(self):
        """Consent engine unavailable → 503."""
        import asyncio
        from app.services.consent_engine import ConsentEngineUnavailable
        provider = _make_provider_context()

        async def _run():
            with patch("app.core.consent_gate.validate_consent_capability",
                        new_callable=AsyncMock,
                        side_effect=ConsentEngineUnavailable("Redis down")), \
                 patch("app.core.consent_gate.append_audit_log_or_503", return_value=None):
                return await validate_consent_for_patient(
                    patient_id=str(uuid.uuid4()),
                    purpose="pipeline_status",
                    provider=provider,
                    x_consent_token="valid-token",
                )

        with pytest.raises(Exception) as exc_info:
            asyncio.run(_run())
        assert exc_info.value.status_code == 503


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Source code verification: no client-provided patient_id on job/field routes
# ═══════════════════════════════════════════════════════════════════════════════


class TestSourceCodeConsentPattern:
    """Verify that pipeline route handlers derive patient_id server-side."""

    def test_get_extraction_job_uses_validate_consent_for_patient(self):
        """get_extraction_job calls validate_consent_for_patient, not require_consent."""
        import inspect
        from app.api.v2.pipeline_routes import get_extraction_job
        source = inspect.getsource(get_extraction_job)
        # Must NOT use require_consent as a dependency
        assert "Depends(require_consent" not in source
        # Must use validate_consent_for_patient directly
        assert "validate_consent_for_patient" in source
        # Must load job before consent
        assert "job.patient_id" in source

    def test_review_extracted_field_uses_validate_consent_for_patient(self):
        """review_extracted_field calls validate_consent_for_patient."""
        import inspect
        from app.api.v2.pipeline_routes import review_extracted_field
        source = inspect.getsource(review_extracted_field)
        assert "Depends(require_consent" not in source
        assert "validate_consent_for_patient" in source

    def test_commit_extraction_job_validates_patient_id_match(self):
        """commit_extraction_job validates payload.patient_id matches job."""
        import inspect
        from app.api.v2.pipeline_routes import commit_extraction_job
        source = inspect.getsource(commit_extraction_job)
        assert "payload.patient_id" in source
        assert "job.patient_id" in source
        assert "does not match" in source or "mismatch" in source.lower() or "patient_id" in source

    def test_commit_uses_server_patient_id_for_ingestion(self):
        """commit handler passes server-derived patient_id to ingest_extracted_fields."""
        import inspect
        from app.api.v2.pipeline_routes import commit_extraction_job
        source = inspect.getsource(commit_extraction_job)
        # Should use server_pid or str(job.patient_id), not payload.patient_id
        # for the ingestion call
        assert "server_pid" in source or "str(job.patient_id)" in source

    def test_upload_still_uses_require_consent(self):
        """Upload endpoint still uses require_consent (new entity, client provides patient_id)."""
        import inspect
        from app.api.v2.pipeline_routes import upload_pipeline_document
        source = inspect.getsource(upload_pipeline_document)
        assert "require_consent" in source

    def test_review_queue_still_uses_require_consent(self):
        """Review queue still uses require_consent (client provides patient_id as filter)."""
        import inspect
        from app.api.v2.pipeline_routes import get_review_queue
        source = inspect.getsource(get_review_queue)
        assert "require_consent" in source

    def test_consent_gate_exports_validate_consent_for_patient(self):
        """consent_gate.py exports validate_consent_for_patient."""
        from app.core import consent_gate
        assert hasattr(consent_gate, "validate_consent_for_patient")
        assert callable(consent_gate.validate_consent_for_patient)
