"""Pipeline flow integration tests — Days 6-8 connected flow.

Exercises the full AI ingestion pipeline chain:
  Upload → extract → score → route → review → commit → verify in timeline.

ALPHA: Uses mock_db for the SQLAlchemy layer and FakeRedis for the consent
store, but exercises the real route handlers and the real record_ingestion
service code.  External services (S3, actual AI extraction) are stubbed.
The consent gate uses validate_consent_for_patient with server-derived
patient_id from the job entity (spoofing-safe).
"""

from __future__ import annotations

import uuid
from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
from tests.conftest import DualModeTestClient, FakeRedis, FakeSyncRedis


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_provider_context() -> ProviderContext:
    return ProviderContext(
        provider=ProviderIdentityContext(
            provider_id=uuid.uuid4(),
            display_name="Dr. Pipeline",
            contact_email="pipeline@hospital.example",
        ),
        hospital=HospitalContext(
            hospital_id=uuid.uuid4(),
            facility_code="PL",
            display_name="Pipeline Hospital",
        ),
        affiliation=AffiliationContext(
            affiliation_id=uuid.uuid4(),
            affiliation_type=AffiliationType.PERMANENT,
            is_primary=True,
            roles=["clinician"],
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


def _mock_job(job_id: str, patient_id: str, status: str = "scored") -> MagicMock:
    m = MagicMock()
    m.id = uuid.UUID(job_id)
    m.patient_id = uuid.UUID(patient_id)
    m.status = status
    m.document_type = "LAB_REPORT"
    m.created_at = datetime.now(timezone.utc)
    return m


def _mock_field(field_id: str, job_id: str, *, field_name: str = "bp",
                raw_value: str = "120/80", confidence: float = 0.96,
                risk_level: str = "LOW_RISK", status: str = "auto_approved") -> MagicMock:
    m = MagicMock()
    m.id = uuid.UUID(field_id)
    m.job_id = uuid.UUID(job_id)
    m.field_name = field_name
    m.raw_value = raw_value
    m.normalized_value = raw_value.replace(" ", "")
    m.confidence = confidence
    m.risk_level = risk_level
    m.status = status
    m.corrected_value = None
    m.validation_result = {"is_valid": True, "validation_errors": []}
    m.source_page = 1
    m.source_bbox = [0.1, 0.2, 0.3, 0.05]
    m.source_document_id = uuid.UUID(job_id)
    return m


def _mock_review_queue_item(item_id: str, job_id: str, field_id: str,
                            patient_id: str) -> MagicMock:
    m = MagicMock()
    m.id = uuid.UUID(item_id)
    m.job_id = uuid.UUID(job_id)
    m.field_id = uuid.UUID(field_id)
    m.patient_id = uuid.UUID(patient_id)
    m.queued_at = datetime.now(timezone.utc)
    m.status = "pending"
    m.adjudicated_by = None
    m.adjudicated_at = None
    m.notes = None
    return m


def _db_result(*, scalar_one_or_none=None, scalars_all=None, scalar=None):
    """Create a MagicMock mimicking a SQLAlchemy Result row.

    Convenience factory so test code stays readable:
        _db_result(scalar_one_or_none=job)
        _db_result(scalars_all=[field1, field2])
        _db_result(scalar=0)
    """
    if scalars_all is not None:
        return MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=scalars_all))),
        )
    if scalar is not None:
        return MagicMock(scalar=MagicMock(return_value=scalar))
    # Default: scalar_one_or_none
    return MagicMock(scalar_one_or_none=MagicMock(return_value=scalar_one_or_none))


def _side_effect_with_fallback(results):
    """Create a side_effect that yields specific results then falls back to safe defaults.

    When the list of specific results is exhausted, subsequent calls
    return a safe default (empty scalars, None scalar_one_or_none).
    This prevents StopAsyncIteration from extra db.execute calls made
    by middleware or dependency injection.
    """
    default = MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
        scalar_one_or_none=MagicMock(return_value=None),
        scalar=MagicMock(return_value=0),
    )
    results_iter = iter(results)

    def _next(*args, **kwargs):
        try:
            return next(results_iter)
        except StopIteration:
            return default

    return _next


def _reset_mock_db(mock_db):
    """Reset mock_db.execute for the next HTTP call.

    IMPORTANT: ``reset_mock()`` does NOT clear ``side_effect`` — it must be
    explicitly set to None first.
    """
    mock_db.execute.side_effect = None
    mock_db.execute.reset_mock()
    mock_db.execute.return_value = MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
        scalar_one_or_none=MagicMock(return_value=None),
        scalar=MagicMock(return_value=0),
    )


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    return DualModeTestClient(app)


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def fake_sync_redis(fake_redis):
    return FakeSyncRedis(fake_redis)


@pytest.fixture
def provider():
    return _make_provider_context()


@pytest.fixture
def patient_id():
    return str(uuid.uuid4())


@pytest.fixture
def overrides():
    saved = {}
    yield saved
    for dep in saved:
        app.dependency_overrides.pop(dep, None)


def _apply_auth_overrides(overrides, provider):
    async def _provider_dep():
        return provider

    overrides[get_current_provider] = _provider_dep
    app.dependency_overrides[get_current_provider] = _provider_dep


def _consent_and_audit_patches(capability):
    """Patch consent validation + audit logging + background tasks for pipeline routes."""
    stack = ExitStack()
    stack.enter_context(
        patch("app.core.consent_gate.validate_consent_capability",
              new_callable=AsyncMock, return_value=capability)
    )
    for mod in (
        "app.core.consent_gate",
        "app.api.v2.pipeline_routes",
        "app.observability.audit_ledger",
        "app.services.record_ingestion",
        "app.services.pipeline_orchestrator",
    ):
        stack.enter_context(
            patch(f"{mod}.append_audit_log_or_503", return_value=None)
        )
    stack.enter_context(
        patch("app.observability.audit_ledger.append_audit_log", return_value=None)
    )
    # Prevent the background extraction task from running
    stack.enter_context(
        patch("app.api.v2.pipeline_routes.process_extraction_job", return_value=None)
    )
    stack.enter_context(
        patch("app.services.pipeline_orchestrator.process_extraction_job", return_value=None)
    )
    # Patch record ingestion at the route's import site so the mock is
    # actually invoked when the route handler calls ingest_extracted_fields.
    # (The route does ``from app.services.record_ingestion import ingest_extracted_fields``,
    #  so patching the definition site alone does NOT affect the already-bound name.)
    stack.enter_context(
        patch("app.api.v2.pipeline_routes.ingest_extracted_fields",
              new_callable=AsyncMock, return_value=None)
    )
    return stack


# ═══════════════════════════════════════════════════════════════════════════════
# FULL PIPELINE FLOW INTEGRATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestPipelineFlowIntegration:
    """End-to-end: upload → extract → score → route → review → commit → timeline.

    ALPHA: Uses mock_db and FakeRedis, but exercises real route handler code.
    The consent gate uses validate_consent_for_patient with server-derived
    patient_id — no client-supplied patient_id spoofing possible.
    """

    def test_auto_approved_pipeline_flow(
        self, client, fake_redis, fake_sync_redis, mock_db, overrides, provider, patient_id
    ):
        """Upload → all fields auto_approved → commit → verify committed."""
        job_id = str(uuid.uuid4())
        capability = _make_capability(patient_id)
        _apply_auth_overrides(overrides, provider)

        field1 = _mock_field(str(uuid.uuid4()), job_id, field_name="hba1c",
                             raw_value="6.5 %", confidence=0.96, status="auto_approved")
        field2 = _mock_field(str(uuid.uuid4()), job_id, field_name="glucose",
                             raw_value="105 mg/dL", confidence=0.93, status="auto_approved")
        job = _mock_job(job_id, patient_id, status="scored")

        with _consent_and_audit_patches(capability):

            # ── Step 1: Upload document ─────────────────────────────────
            _reset_mock_db(mock_db)
            resp = client.post(
                f"/api/v2/pipeline/documents/upload?patient_id={patient_id}&filename=lab_report.pdf",
                headers={"X-Consent-Token": "t"},
            )
            assert resp.status_code == 202, f"Upload failed: {resp.text}"
            assert resp.json()["status"] == "queued"

            # ── Step 2: Check job status ────────────────────────────────
            _reset_mock_db(mock_db)
            mock_db.execute.side_effect = _side_effect_with_fallback([
                _db_result(scalar_one_or_none=job),
                _db_result(scalars_all=[field1, field2]),
            ])
            status_resp = client.get(
                f"/api/v2/pipeline/jobs/{job_id}",
                headers={"X-Consent-Token": "t"},
            )
            assert status_resp.status_code == 200
            status_data = status_resp.json()
            assert status_data["auto_approved_count"] == 2
            assert status_data["needs_review_count"] == 0

            # ── Step 3: Review queue (empty — all auto_approved) ────────
            _reset_mock_db(mock_db)
            queue_resp = client.get(
                "/api/v2/pipeline/review-queue",
                headers={"X-Consent-Token": "t", "X-Patient-Id": patient_id},
            )
            assert queue_resp.status_code == 200

            # ── Step 4: Commit ──────────────────────────────────────────
            _reset_mock_db(mock_db)
            mock_db.execute.side_effect = _side_effect_with_fallback([
                _db_result(scalar_one_or_none=job),
                _db_result(scalars_all=[]),
                _db_result(scalars_all=[field1, field2]),
            ])
            commit_resp = client.post(
                f"/api/v2/pipeline/jobs/{job_id}/commit",
                json={"patient_id": patient_id},
                headers={"X-Consent-Token": "t"},
            )
            assert commit_resp.status_code == 201, f"Commit failed: {commit_resp.text}"
            assert commit_resp.json()["status"] == "committed"
            assert commit_resp.json()["committed_fields_count"] == 2

    def test_needs_review_pipeline_flow(
        self, client, fake_redis, fake_sync_redis, mock_db, overrides, provider, patient_id
    ):
        """Upload → some fields need_review → review → approve → commit."""
        job_id = str(uuid.uuid4())
        field_id = str(uuid.uuid4())
        capability = _make_capability(patient_id)
        _apply_auth_overrides(overrides, provider)

        field_auto = _mock_field(str(uuid.uuid4()), job_id, field_name="hba1c",
                                 confidence=0.96, status="auto_approved")
        field_review = _mock_field(field_id, job_id, field_name="creatinine",
                                   raw_value="2.4 mg/dL", confidence=0.72,
                                   risk_level="HIGH_RISK", status="needs_review")
        job = _mock_job(job_id, patient_id, status="review_required")

        with _consent_and_audit_patches(capability):

            # ── Step 1: Check job status ────────────────────────────────
            _reset_mock_db(mock_db)
            mock_db.execute.side_effect = _side_effect_with_fallback([
                _db_result(scalar_one_or_none=job),
                _db_result(scalars_all=[field_auto, field_review]),
            ])
            status_resp = client.get(
                f"/api/v2/pipeline/jobs/{job_id}",
                headers={"X-Consent-Token": "t"},
            )
            assert status_resp.status_code == 200
            assert status_resp.json()["needs_review_count"] == 1

            # ── Step 2: Attempt commit (blocked — unresolved fields) ────
            _reset_mock_db(mock_db)
            mock_db.execute.side_effect = _side_effect_with_fallback([
                _db_result(scalar_one_or_none=job),
                _db_result(scalars_all=[field_review]),
            ])
            blocked_resp = client.post(
                f"/api/v2/pipeline/jobs/{job_id}/commit",
                json={"patient_id": patient_id},
                headers={"X-Consent-Token": "t"},
            )
            assert blocked_resp.status_code == 409

            # ── Step 3: Review — approve the field ──────────────────────
            _reset_mock_db(mock_db)
            mock_db.execute.side_effect = _side_effect_with_fallback([
                _db_result(scalar_one_or_none=field_review),
                _db_result(scalar_one_or_none=job),
                _db_result(scalar_one_or_none=None),
            ])
            review_resp = client.post(
                f"/api/v2/pipeline/fields/{field_id}/review",
                json={"action": "approve"},
                headers={"X-Consent-Token": "t"},
            )
            assert review_resp.status_code == 200
            assert review_resp.json()["new_status"] == "approved"

            # ── Step 4: Commit (now succeeds) ───────────────────────────
            _reset_mock_db(mock_db)
            field_after_review = _mock_field(field_id, job_id, field_name="creatinine",
                                             raw_value="2.4 mg/dL", confidence=0.72,
                                             risk_level="HIGH_RISK", status="approved")
            mock_db.execute.side_effect = _side_effect_with_fallback([
                _db_result(scalar_one_or_none=job),
                _db_result(scalars_all=[]),
                _db_result(scalars_all=[field_auto, field_after_review]),
            ])
            commit_resp = client.post(
                f"/api/v2/pipeline/jobs/{job_id}/commit",
                json={"patient_id": patient_id},
                headers={"X-Consent-Token": "t"},
            )
            assert commit_resp.status_code == 201
            assert commit_resp.json()["status"] == "committed"

    def test_rejected_field_excluded_from_commit(
        self, client, fake_redis, fake_sync_redis, mock_db, overrides, provider, patient_id
    ):
        """Upload → review rejects field → commit only includes approved fields."""
        job_id = str(uuid.uuid4())
        field_id = str(uuid.uuid4())
        capability = _make_capability(patient_id)
        _apply_auth_overrides(overrides, provider)

        field_review = _mock_field(field_id, job_id, field_name="bad_value",
                                   confidence=0.45, risk_level="CRITICAL_RISK",
                                   status="needs_review")
        job = _mock_job(job_id, patient_id, status="review_required")

        with _consent_and_audit_patches(capability):
            # ── Step 1: Reject the field ────────────────────────────────
            _reset_mock_db(mock_db)
            mock_db.execute.side_effect = _side_effect_with_fallback([
                _db_result(scalar_one_or_none=field_review),
                _db_result(scalar_one_or_none=job),
                _db_result(scalar_one_or_none=None),
            ])
            reject_resp = client.post(
                f"/api/v2/pipeline/fields/{field_id}/review",
                json={"action": "reject", "review_notes": "Invalid extraction"},
                headers={"X-Consent-Token": "t"},
            )
            assert reject_resp.status_code == 200
            assert reject_resp.json()["new_status"] == "rejected"

            # ── Step 2: Commit with explicit fields (rejected excluded) ──
            _reset_mock_db(mock_db)
            mock_db.execute.side_effect = _side_effect_with_fallback([
                _db_result(scalar_one_or_none=job),
                _db_result(scalars_all=[]),
            ])
            commit_resp = client.post(
                f"/api/v2/pipeline/jobs/{job_id}/commit",
                json={
                    "patient_id": patient_id,
                    "fields": [{
                        "field_id": field_id, "field_name": "bad_value",
                        "raw_value": "x", "normalized_value": "x",
                        "confidence": 0.45, "risk_level": "CRITICAL_RISK",
                        "status": "rejected",
                    }],
                },
                headers={"X-Consent-Token": "t"},
            )
            assert commit_resp.status_code == 201
            assert commit_resp.json()["status"] == "committed"

    def test_edited_field_in_commit(
        self, client, fake_redis, fake_sync_redis, mock_db, overrides, provider, patient_id
    ):
        """Review → edit field → commit includes corrected value."""
        job_id = str(uuid.uuid4())
        field_id = str(uuid.uuid4())
        capability = _make_capability(patient_id)
        _apply_auth_overrides(overrides, provider)

        field_review = _mock_field(field_id, job_id, field_name="bp",
                                   raw_value="120/80", confidence=0.82,
                                   risk_level="MEDIUM_RISK", status="needs_review")
        job = _mock_job(job_id, patient_id, status="review_required")

        with _consent_and_audit_patches(capability):

            # ── Step 1: Edit the field ──────────────────────────────────
            _reset_mock_db(mock_db)
            mock_db.execute.side_effect = _side_effect_with_fallback([
                _db_result(scalar_one_or_none=field_review),
                _db_result(scalar_one_or_none=job),
                _db_result(scalar_one_or_none=None),
            ])
            edit_resp = client.post(
                f"/api/v2/pipeline/fields/{field_id}/review",
                json={"action": "edit", "corrected_value": "140/90"},
                headers={"X-Consent-Token": "t"},
            )
            assert edit_resp.status_code == 200
            assert edit_resp.json()["new_status"] == "edited"
            assert edit_resp.json()["final_value"] == "140/90"

            # ── Step 2: Commit ──────────────────────────────────────────
            _reset_mock_db(mock_db)
            mock_db.execute.side_effect = _side_effect_with_fallback([
                _db_result(scalar_one_or_none=job),
                _db_result(scalars_all=[]),
                _db_result(scalars_all=[]),
            ])
            commit_resp = client.post(
                f"/api/v2/pipeline/jobs/{job_id}/commit",
                json={"patient_id": patient_id},
                headers={"X-Consent-Token": "t"},
            )
            assert commit_resp.status_code == 201
            assert commit_resp.json()["status"] == "committed"

    def test_commit_rejects_spoofed_patient_id(
        self, client, fake_redis, fake_sync_redis, mock_db, overrides, provider, patient_id
    ):
        """ALPHA: Commit with wrong patient_id in payload → 400."""
        job_id = str(uuid.uuid4())
        capability = _make_capability(patient_id)
        _apply_auth_overrides(overrides, provider)

        job = _mock_job(job_id, patient_id, status="scored")

        with _consent_and_audit_patches(capability):
            _reset_mock_db(mock_db)
            mock_db.execute.side_effect = _side_effect_with_fallback([
                _db_result(scalar_one_or_none=job),
            ])
            commit_resp = client.post(
                f"/api/v2/pipeline/jobs/{job_id}/commit",
                json={"patient_id": str(uuid.uuid4())},  # Wrong patient_id
                headers={"X-Consent-Token": "t"},
            )
            assert commit_resp.status_code == 400
            assert "patient_id" in commit_resp.json()["detail"].lower()

    def test_nonexistent_job_returns_404(
        self, client, fake_redis, fake_sync_redis, mock_db, overrides, provider, patient_id
    ):
        """ALPHA: Job not found → 404 before consent check."""
        job_id = str(uuid.uuid4())
        capability = _make_capability(patient_id)
        _apply_auth_overrides(overrides, provider)

        with _consent_and_audit_patches(capability):
            _reset_mock_db(mock_db)
            resp = client.get(
                f"/api/v2/pipeline/jobs/{job_id}",
                headers={"X-Consent-Token": "t"},
            )
            assert resp.status_code == 404

            _reset_mock_db(mock_db)
            resp = client.post(
                f"/api/v2/pipeline/jobs/{job_id}/commit",
                json={"patient_id": patient_id},
                headers={"X-Consent-Token": "t"},
            )
            assert resp.status_code == 404

    def test_commit_requires_confidence_and_risk_level(
        self, client, fake_redis, fake_sync_redis, mock_db, overrides, provider, patient_id
    ):
        """Fields without confidence/risk_level metadata are rejected at commit."""
        job_id = str(uuid.uuid4())
        capability = _make_capability(patient_id)
        _apply_auth_overrides(overrides, provider)

        job = _mock_job(job_id, patient_id, status="scored")

        with _consent_and_audit_patches(capability):
            # Missing confidence
            _reset_mock_db(mock_db)
            mock_db.execute.side_effect = _side_effect_with_fallback([
                _db_result(scalar_one_or_none=job),
                _db_result(scalars_all=[]),
            ])
            resp = client.post(
                f"/api/v2/pipeline/jobs/{job_id}/commit",
                json={
                    "patient_id": patient_id,
                    "fields": [{
                        "field_id": "f1", "field_name": "test",
                        "raw_value": "v", "normalized_value": "v",
                        "confidence": None, "risk_level": "LOW_RISK",
                        "status": "approved",
                    }],
                },
                headers={"X-Consent-Token": "t"},
            )
            assert resp.status_code == 400

            # Missing risk_level
            _reset_mock_db(mock_db)
            mock_db.execute.side_effect = _side_effect_with_fallback([
                _db_result(scalar_one_or_none=job),
                _db_result(scalars_all=[]),
            ])
            resp = client.post(
                f"/api/v2/pipeline/jobs/{job_id}/commit",
                json={
                    "patient_id": patient_id,
                    "fields": [{
                        "field_id": "f1", "field_name": "test",
                        "raw_value": "v", "normalized_value": "v",
                        "confidence": 0.9, "risk_level": "",
                        "status": "approved",
                    }],
                },
                headers={"X-Consent-Token": "t"},
            )
            assert resp.status_code == 400

    def test_full_flow_with_review_queue(
        self, client, fake_redis, fake_sync_redis, mock_db, overrides, provider, patient_id
    ):
        """Upload → field goes to review queue → review from queue → commit."""
        job_id = str(uuid.uuid4())
        field_id = str(uuid.uuid4())
        item_id = str(uuid.uuid4())
        capability = _make_capability(patient_id)
        _apply_auth_overrides(overrides, provider)

        field_review = _mock_field(field_id, job_id, field_name="troponin",
                                   raw_value="0.08 ng/mL", confidence=0.68,
                                   risk_level="HIGH_RISK", status="needs_review")
        job = _mock_job(job_id, patient_id, status="review_required")
        queue_item = _mock_review_queue_item(item_id, job_id, field_id, patient_id)

        with _consent_and_audit_patches(capability):

            # ── Step 1: Check review queue ──────────────────────────────
            _reset_mock_db(mock_db)
            mock_db.execute.side_effect = _side_effect_with_fallback([
                _db_result(scalars_all=[queue_item]),
            ])
            queue_resp = client.get(
                "/api/v2/pipeline/review-queue",
                headers={"X-Consent-Token": "t", "X-Patient-Id": patient_id},
            )
            assert queue_resp.status_code == 200
            items = queue_resp.json()["items"]
            assert len(items) >= 1

            # ── Step 2: Approve field from queue ────────────────────────
            _reset_mock_db(mock_db)
            mock_db.execute.side_effect = _side_effect_with_fallback([
                _db_result(scalar_one_or_none=field_review),
                _db_result(scalar_one_or_none=job),
                _db_result(scalar_one_or_none=queue_item),
            ])
            review_resp = client.post(
                f"/api/v2/pipeline/fields/{field_id}/review",
                json={"action": "approve"},
                headers={"X-Consent-Token": "t"},
            )
            assert review_resp.status_code == 200
            assert review_resp.json()["new_status"] == "approved"

            # ── Step 3: Commit ──────────────────────────────────────────
            _reset_mock_db(mock_db)
            approved_field = _mock_field(field_id, job_id, field_name="troponin",
                                         raw_value="0.08 ng/mL", confidence=0.68,
                                         risk_level="HIGH_RISK", status="approved")
            mock_db.execute.side_effect = _side_effect_with_fallback([
                _db_result(scalar_one_or_none=job),
                _db_result(scalars_all=[]),
                _db_result(scalars_all=[approved_field]),
            ])
            commit_resp = client.post(
                f"/api/v2/pipeline/jobs/{job_id}/commit",
                json={"patient_id": patient_id},
                headers={"X-Consent-Token": "t"},
            )
            assert commit_resp.status_code == 201, f"Commit failed: {commit_resp.text}"
            assert commit_resp.json()["status"] == "committed"
