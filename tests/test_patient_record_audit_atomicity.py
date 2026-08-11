"""Regression tests for clinical-write audit atomicity and tenant scoping."""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v2.patient_record_routes import (
    AppendAllergyRequest,
    AppendDocumentRequest,
    AppendLabResultRequest,
    AppendMedicationRequest,
    AppendVitalsRequest,
    append_allergies,
    append_documents,
    append_labs,
    append_medications,
    append_vitals,
    get_patient_audit_trail,
)
from app.security.audit_context import AuditContext, AuditDomain


PATIENT_ID = str(uuid4())
PROVIDER = SimpleNamespace(actor_uid=str(uuid4()))
AUDIT_CONTEXT = AuditContext.for_hospital(
    hospital_id=str(uuid4()), domain=AuditDomain.PATIENT_RECORD
)


def _payloads():
    recorded_at = "2026-08-11T00:00:00+00:00"
    return [
        (append_vitals, AppendVitalsRequest(
            systolic_bp=120,
            diastolic_bp=80,
            heart_rate=70,
            temperature_celsius=37,
            sp_o2_percentage=98,
            recorded_at=recorded_at,
        )),
        (append_medications, AppendMedicationRequest(
            name="sample medication",
            strength="10 mg",
            frequency="daily",
            prescribed_at=recorded_at,
        )),
        (append_labs, AppendLabResultRequest(
            test_name="sample lab",
            value="normal",
            unit="unit",
            reference_range="normal",
            recorded_at=recorded_at,
        )),
        (append_allergies, AppendAllergyRequest(
            allergen="sample allergen",
            severity="high",
        )),
        (append_documents, AppendDocumentRequest(
            document_type="sample document",
            storage_ref="tenant/patient/document",
        )),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("route,payload", _payloads())
async def test_every_append_rolls_back_when_outbox_staging_fails(route, payload):
    db = AsyncMock()
    db.add = Mock()
    outbox = AsyncMock(side_effect=RuntimeError("outbox unavailable"))

    with (
        patch(
            "app.api.v2.patient_record_routes.append_audit_log_or_503",
            new=AsyncMock(),
        ),
        patch(
            "app.api.v2.patient_record_routes.current_audit_context",
            return_value=AUDIT_CONTEXT,
        ),
        patch("app.api.v2.patient_record_routes.enqueue_audit_event", new=outbox),
    ):
        with pytest.raises(HTTPException) as exc:
            await route(PATIENT_ID, payload, PROVIDER, object(), db)

    assert exc.value.status_code == 503
    assert exc.value.detail == {"error_code": "AUDIT_DURABILITY_UNAVAILABLE"}
    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("route,payload", _payloads())
async def test_every_append_commits_clinical_timeline_and_outbox_once(route, payload):
    db = AsyncMock()
    db.add = Mock()
    outbox = AsyncMock()

    with (
        patch(
            "app.api.v2.patient_record_routes.append_audit_log_or_503",
            new=AsyncMock(),
        ),
        patch(
            "app.api.v2.patient_record_routes.current_audit_context",
            return_value=AUDIT_CONTEXT,
        ),
        patch("app.api.v2.patient_record_routes.enqueue_audit_event", new=outbox),
    ):
        result = await route(PATIENT_ID, payload, PROVIDER, object(), db)

    assert result["status"] == "committed"
    db.commit.assert_awaited_once()
    db.rollback.assert_not_awaited()
    outbox.assert_awaited_once()
    assert outbox.await_args.kwargs["event_type"] == "PATIENT_RECORD_APPEND_SUCCESS"
    assert outbox.await_args.kwargs["patient_id"] == PATIENT_ID
    assert outbox.await_args.kwargs["idempotency_key"].startswith(
        "patient-record-append:"
    )


@pytest.mark.asyncio
async def test_commit_failure_rolls_back_the_entire_transaction():
    from app.api.v2.patient_record_routes import _commit_patient_record_transaction

    db = AsyncMock()
    db.commit.side_effect = RuntimeError("commit failed")

    with pytest.raises(HTTPException) as exc:
        await _commit_patient_record_transaction(db)

    assert exc.value.status_code == 503
    assert exc.value.detail == {"error_code": "PATIENT_RECORD_WRITE_UNAVAILABLE"}
    db.rollback.assert_awaited_once()


@pytest.mark.parametrize(
    "route",
    [append_vitals, append_medications, append_labs, append_allergies, append_documents],
)
def test_append_routes_stage_outbox_before_their_single_commit(route):
    source = inspect.getsource(route)
    assert "await db.commit()" not in source
    assert "PATIENT_RECORD_APPEND_SUCCESS" not in source
    assert source.count("_stage_patient_record_success_audit") == 1
    assert source.count("_commit_patient_record_transaction") == 1
    assert source.index("_stage_patient_record_success_audit") < source.index(
        "_commit_patient_record_transaction"
    )


@pytest.mark.asyncio
async def test_admin_audit_trail_passes_authenticated_hospital_scope():
    hospital_id = uuid4()
    provider = SimpleNamespace(hospital=SimpleNamespace(hospital_id=hospital_id))
    read = AsyncMock(return_value=[])

    with patch("app.api.v2.patient_record_routes.read_audit_events", new=read):
        result = await get_patient_audit_trail(PATIENT_ID, provider=provider)

    assert result["audit_trail"] == []
    context = read.await_args.kwargs["audit_context"]
    assert context.hospital_id == str(hospital_id)
    assert context.tenant_id is None
    assert context.platform_global is False


def test_audit_target_query_requires_trusted_chain_scope():
    from app.observability import audit_ledger

    query = str(audit_ledger._READ_FOR_TARGET_SQL)
    assert "chain_scope LIKE :chain_scope_prefix" in query
    assert "WHERE resource = :target_id" in query
