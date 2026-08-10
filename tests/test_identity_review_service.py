from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.identity_review import (
    IDENTITY_REVIEW_CONTRACT_VERSION,
    IdentityReviewCaseRecord,
    IdentityReviewDispositionRecord,
    IdentityReviewOperationRecord,
    IdentityReviewOutcome,
    IdentityReviewReasonCode,
)
from app.models.pipeline import (
    DocumentStorage,
    ExtractionCandidateRecord,
    ExtractionDecisionRecord,
    ExtractionJob,
    ExtractionRoutingRecord,
)
from app.models.provider import AffiliationType
from app.models.provider_context import (
    AffiliationContext,
    HospitalContext,
    ProviderContext,
    ProviderIdentityContext,
)
from app.security.identity_review_policy import IDENTITY_REVIEW_POLICY_VERSION
from app.services.identity_review import (
    IdentityReviewError,
    _existing_operation,
    _load_graph,
    _session_binding_matches,
    claim_case,
    create_case,
    recover_session,
    submit_disposition,
)


def _provider(*, actor_id=None, hospital_id=None, session="a" * 64):
    actor_id = actor_id or uuid.uuid4()
    hospital_id = hospital_id or uuid.uuid4()
    return ProviderContext(
        provider=ProviderIdentityContext(
            provider_id=actor_id,
            display_name="Identity Reviewer",
            contact_email="identity-reviewer@example.test",
        ),
        hospital=HospitalContext(
            hospital_id=hospital_id,
            facility_code="TEST",
            display_name="Test Hospital",
        ),
        affiliation=AffiliationContext(
            affiliation_id=uuid.uuid4(),
            affiliation_type=AffiliationType.PERMANENT,
            roles=["identity_reviewer"],
            is_primary=True,
        ),
        session_binding=session,
    )


def _job(*, patient_id=None, tenant_id=None, error_code="EXTRACTED_IDENTITY_MISMATCH"):
    return ExtractionJob(
        id=uuid.uuid4(),
        patient_id=patient_id or uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        uploader_id=str(uuid.uuid4()),
        authorization_provider_id=str(uuid.uuid4()),
        consent_request_id="original-request",
        document_id=uuid.uuid4(),
        document_type="lab_report",
        status="quarantined",
        request_id=str(uuid.uuid4()),
        attempt_count=1,
        error_code=error_code,
        retryable=False,
        version=1,
        created_at=datetime.now(timezone.utc),
    )


def _document(job):
    return DocumentStorage(
        id=job.document_id,
        patient_id=job.patient_id,
        tenant_id=job.tenant_id,
        uploader_id=job.uploader_id,
        storage_ref="opaque-storage-ref",
        content_type="application/pdf",
        size=123,
        uploaded_at=datetime.now(timezone.utc),
    )


def _case(provider, job, *, status="PENDING", version=1, session=None):
    assigned = provider.actor_uid if status != "PENDING" else None
    now = datetime.now(timezone.utc)
    return IdentityReviewCaseRecord(
        id=uuid.uuid4(),
        job_id=job.id,
        patient_id=job.patient_id,
        tenant_id=job.tenant_id,
        source_document_id=job.document_id,
        original_uploader_id=job.uploader_id,
        original_authorization_provider_id=job.authorization_provider_id,
        source_consent_request_id=job.consent_request_id,
        identity_reason_codes=["DOCUMENT_IDENTITY_MISMATCH"],
        assigned_reviewer_id=assigned,
        assigned_reviewer_role="identity_reviewer" if assigned else None,
        review_session_binding=session if assigned else None,
        status=status,
        version=version,
        creation_idempotency_key="create-key-0001",
        creation_operation_hash="1" * 64,
        contract_version=IDENTITY_REVIEW_CONTRACT_VERSION,
        policy_version=IDENTITY_REVIEW_POLICY_VERSION,
        created_at=now,
        claimed_at=now if assigned else None,
        resolved_at=None,
    )


def _scalar_result(value):
    return MagicMock(scalar_one_or_none=MagicMock(return_value=value))


def _scalars_result(values):
    return MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=values)))
    )


def _db(*, execute_results=()):
    db = MagicMock()
    db.execute = AsyncMock(side_effect=list(execute_results))
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.add = MagicMock()
    return db


def _route_decision(job, *, lane="QUARANTINE", auto_commit=False):
    decision_id = uuid.uuid4()
    decision = SimpleNamespace(
        id=decision_id,
        job_id=job.id,
        patient_id=job.patient_id,
        tenant_id=job.tenant_id,
        source_document_id=job.document_id,
        lane=lane,
        reason_codes=["IDENTITY_MISMATCH"],
        auto_commit_feature_enabled=auto_commit,
    )
    route = SimpleNamespace(
        id=uuid.uuid4(),
        decision_id=decision_id,
        job_id=job.id,
        patient_id=job.patient_id,
        tenant_id=job.tenant_id,
        source_document_id=job.document_id,
        lane=lane,
        status="QUARANTINE_PENDING" if lane == "QUARANTINE" else "SOURCE_RETAINED",
    )
    return route, decision


def _authoritative_binding_snapshot(
    job: ExtractionJob,
    document: DocumentStorage,
    candidate: ExtractionCandidateRecord,
    decision: ExtractionDecisionRecord,
    route: ExtractionRoutingRecord,
):
    resources = (
        ("job", job, ("id", "patient_id", "tenant_id", "document_id")),
        ("document", document, ("id", "patient_id", "tenant_id")),
        (
            "candidate",
            candidate,
            ("id", "job_id", "source_document_id", "patient_id", "tenant_id"),
        ),
        (
            "decision",
            decision,
            ("id", "job_id", "source_document_id", "patient_id", "tenant_id"),
        ),
        (
            "route",
            route,
            ("id", "job_id", "source_document_id", "patient_id", "tenant_id"),
        ),
    )
    return tuple(
        (f"{name}.{field}", getattr(resource, field))
        for name, resource, fields in resources
        for field in fields
    )


@pytest.mark.asyncio
async def test_graph_supports_zero_candidate_identity_quarantine():
    job = _job(error_code="EXTRACTED_IDENTITY_UNAVAILABLE")
    db = _db(
        execute_results=[
            _scalar_result(_document(job)),
            _scalars_result([]),
            _scalars_result([]),
        ]
    )
    _, routes, decisions, reasons = await _load_graph(db, job=job, lock=True)
    assert routes == []
    assert decisions == []
    assert reasons == (IdentityReviewReasonCode.CANONICAL_IDENTITY_UNAVAILABLE,)


@pytest.mark.asyncio
async def test_graph_binds_every_route_and_rejects_source_only_auto_commit_or_mismatch():
    job = _job()
    route, decision = _route_decision(job)
    valid = _db(
        execute_results=[
            _scalar_result(_document(job)),
            _scalars_result([route]),
            _scalars_result([decision]),
        ]
    )
    _, routes, decisions, _ = await _load_graph(valid, job=job, lock=True)
    assert routes == [route]
    assert decisions == [decision]

    for mutate in ("source_only", "auto_commit", "cross_patient"):
        bad_route, bad_decision = _route_decision(job)
        if mutate == "source_only":
            bad_route.lane = "SOURCE_ONLY"
            bad_route.status = "SOURCE_RETAINED"
            bad_decision.lane = "SOURCE_ONLY"
        elif mutate == "auto_commit":
            bad_decision.auto_commit_feature_enabled = True
        else:
            bad_route.patient_id = uuid.uuid4()
        db = _db(
            execute_results=[
                _scalar_result(_document(job)),
                _scalars_result([bad_route]),
                _scalars_result([bad_decision]),
            ]
        )
        with pytest.raises(IdentityReviewError) as caught:
            await _load_graph(db, job=job, lock=True)
        assert caught.value.code == "IDENTITY_REVIEW_BINDING_MISMATCH"


@pytest.mark.asyncio
async def test_non_identity_quarantine_is_ineligible():
    job = _job(error_code="PROVIDER_FAILURE")
    db = _db(
        execute_results=[
            _scalar_result(_document(job)),
            _scalars_result([]),
            _scalars_result([]),
        ]
    )
    with pytest.raises(IdentityReviewError) as caught:
        await _load_graph(db, job=job, lock=True)
    assert caught.value.code == "IDENTITY_REVIEW_JOB_INELIGIBLE"


@pytest.mark.asyncio
@pytest.mark.parametrize("route_count", [0, 3])
async def test_create_one_case_per_job_and_bind_all_routes(route_count):
    job = _job()
    provider = _provider(hospital_id=job.tenant_id)
    document = _document(job)
    pairs = [_route_decision(job) for _ in range(route_count)]
    routes = [pair[0] for pair in pairs]
    decisions = [pair[1] for pair in pairs]
    db = _db(execute_results=[_scalar_result(None), _scalar_result(None)])

    async def assign_ids():
        for call in db.add.call_args_list:
            row = call.args[0]
            if getattr(row, "id", None) is None:
                row.id = uuid.uuid4()

    db.flush.side_effect = assign_ids
    with (
        patch(
            "app.services.identity_review._load_job", new=AsyncMock(return_value=job)
        ),
        patch("app.services.identity_review._authorize", new=AsyncMock()),
        patch(
            "app.services.identity_review._load_graph",
            new=AsyncMock(
                return_value=(
                    document,
                    routes,
                    decisions,
                    (IdentityReviewReasonCode.DOCUMENT_IDENTITY_MISMATCH,),
                )
            ),
        ),
        patch(
            "app.services.identity_review.enqueue_audit_event", new=AsyncMock()
        ) as audit,
    ):
        case = await create_case(
            db,
            job_id=job.id,
            provider=provider,
            capability_token="own-token",
            idempotency_key="create-key-0001",
        )
    assert case.status == "PENDING"
    assert case.version == 1
    assert case.job_id == job.id
    assert case.assigned_reviewer_id is None
    bound = [
        call.args[0]
        for call in db.add.call_args_list
        if call.args[0].__class__.__name__ == "IdentityReviewCaseRouteRecord"
    ]
    assert len(bound) == route_count
    assert {row.routing_id for row in bound} == {route.id for route in routes}
    audit.assert_awaited_once()


@pytest.mark.asyncio
async def test_claim_uses_provider_session_binding_and_is_single_winner_ready():
    job = _job()
    provider = _provider(hospital_id=job.tenant_id, session="b" * 64)
    case = _case(provider, job)
    db = _db()
    with (
        patch(
            "app.services.identity_review._load_case", new=AsyncMock(return_value=case)
        ),
        patch("app.services.identity_review._authorize", new=AsyncMock()),
        patch(
            "app.services.identity_review._existing_operation",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.identity_review._revalidate_case_graph",
            new=AsyncMock(return_value=(job, _document(job))),
        ),
        patch("app.services.identity_review.enqueue_audit_event", new=AsyncMock()),
    ):
        result = await claim_case(
            db,
            case_id=case.id,
            provider=provider,
            capability_token="token",
            expected_version=1,
            idempotency_key="claim-key-0001",
        )
    assert result.status == "IN_REVIEW"
    assert result.assigned_reviewer_id == provider.actor_uid
    assert result.review_session_binding == provider.session_binding
    assert result.version == 2
    operation = next(
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], IdentityReviewOperationRecord)
    )
    assert operation.operation == "CLAIM"
    assert provider.session_binding not in operation.operation_hash


@pytest.mark.asyncio
async def test_session_recovery_rotates_binding_and_old_session_cannot_dispose():
    job = _job()
    old_provider = _provider(hospital_id=job.tenant_id, session="c" * 64)
    new_provider = old_provider.model_copy(update={"session_binding": "d" * 64})
    case = _case(old_provider, job, status="IN_REVIEW", version=2, session="c" * 64)
    db = _db()
    common = (
        patch(
            "app.services.identity_review._load_case", new=AsyncMock(return_value=case)
        ),
        patch("app.services.identity_review._authorize", new=AsyncMock()),
        patch(
            "app.services.identity_review._existing_operation",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.identity_review._revalidate_case_graph",
            new=AsyncMock(return_value=(job, _document(job))),
        ),
        patch("app.services.identity_review.enqueue_audit_event", new=AsyncMock()),
    )
    with common[0], common[1], common[2], common[3], common[4]:
        result = await recover_session(
            db,
            case_id=case.id,
            provider=new_provider,
            capability_token="token",
            expected_version=2,
            idempotency_key="recover-key-0001",
        )
    assert result.review_session_binding == "d" * 64
    assert result.version == 3

    with (
        patch(
            "app.services.identity_review._load_case", new=AsyncMock(return_value=case)
        ),
        patch("app.services.identity_review._authorize", new=AsyncMock()),
        patch(
            "app.services.identity_review._existing_operation",
            new=AsyncMock(return_value=None),
        ),
    ):
        with pytest.raises(IdentityReviewError) as caught:
            await submit_disposition(
                db,
                case_id=case.id,
                provider=old_provider,
                capability_token="token",
                expected_version=3,
                idempotency_key="disposition-key-old-session",
                outcome=IdentityReviewOutcome.INSUFFICIENT_IDENTITY_EVIDENCE,
                reason_codes=(IdentityReviewReasonCode.IDENTITY_REVIEW_INCONCLUSIVE,),
            )
    assert caught.value.code == "IDENTITY_REVIEW_SESSION_MISMATCH"


def test_session_binding_matches_uses_constant_time_comparison():
    with patch(
        "app.services.identity_review.secrets.compare_digest", return_value=True
    ) as compare_digest:
        assert _session_binding_matches("a" * 64, "b" * 64) is True
    compare_digest.assert_called_once_with("a" * 64, "b" * 64)

    assert _session_binding_matches("a" * 64, "c" * 64) is False
    assert _session_binding_matches(None, "a" * 64) is False
    assert _session_binding_matches("a" * 63, "a" * 64) is False


@pytest.mark.parametrize(
    ("outcome", "reason", "expected_state"),
    [
        (
            IdentityReviewOutcome.REJECTED_FOR_BOUND_PATIENT,
            IdentityReviewReasonCode.DOCUMENT_REJECTED_FOR_BOUND_PATIENT,
            "RESOLVED_NO_RELEASE",
        ),
        (
            IdentityReviewOutcome.VERIFIED_IDENTITY_REQUIRED,
            IdentityReviewReasonCode.VERIFIED_IDENTIFIER_REQUIRED,
            "RESOLVED_NO_RELEASE",
        ),
        (
            IdentityReviewOutcome.INSUFFICIENT_IDENTITY_EVIDENCE,
            IdentityReviewReasonCode.IDENTITY_REVIEW_INCONCLUSIVE,
            "RESOLVED_NO_RELEASE",
        ),
        (
            IdentityReviewOutcome.SECURITY_ESCALATION_REQUIRED,
            IdentityReviewReasonCode.POSSIBLE_PRIVACY_INCIDENT,
            "ESCALATED",
        ),
    ],
)
@pytest.mark.asyncio
async def test_every_disposition_is_immutable_non_release(
    outcome, reason, expected_state
):
    job = _job()
    provider = _provider(hospital_id=job.tenant_id, session="e" * 64)
    case = _case(provider, job, status="IN_REVIEW", version=2, session="e" * 64)
    document = _document(job)
    original_error = job.error_code
    original_job_patient = job.patient_id
    original_document_patient = document.patient_id
    db = _db()
    with (
        patch(
            "app.services.identity_review._load_case", new=AsyncMock(return_value=case)
        ),
        patch("app.services.identity_review._authorize", new=AsyncMock()),
        patch(
            "app.services.identity_review._existing_operation",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.identity_review._revalidate_case_graph",
            new=AsyncMock(return_value=(job, document)),
        ),
        patch(
            "app.services.identity_review.enqueue_audit_event", new=AsyncMock()
        ) as audit,
    ):
        disposition = await submit_disposition(
            db,
            case_id=case.id,
            provider=provider,
            capability_token="token",
            expected_version=2,
            idempotency_key=f"disposition-{outcome.value.lower()}",
            outcome=outcome,
            reason_codes=(reason,),
        )
    assert isinstance(disposition, IdentityReviewDispositionRecord)
    assert case.status == expected_state
    assert case.version == 3
    assert job.status == "quarantined"
    assert job.error_code == original_error
    assert job.patient_id == original_job_patient
    assert document.patient_id == original_document_patient
    assert disposition.outcome == outcome.value
    assert audit.await_count == (2 if expected_state == "ESCALATED" else 1)


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [
        (
            IdentityReviewOutcome.REJECTED_FOR_BOUND_PATIENT,
            IdentityReviewReasonCode.DOCUMENT_REJECTED_FOR_BOUND_PATIENT,
        ),
        (
            IdentityReviewOutcome.VERIFIED_IDENTITY_REQUIRED,
            IdentityReviewReasonCode.VERIFIED_IDENTIFIER_REQUIRED,
        ),
        (
            IdentityReviewOutcome.SECURITY_ESCALATION_REQUIRED,
            IdentityReviewReasonCode.POSSIBLE_PRIVACY_INCIDENT,
        ),
        (
            IdentityReviewOutcome.INSUFFICIENT_IDENTITY_EVIDENCE,
            IdentityReviewReasonCode.IDENTITY_REVIEW_INCONCLUSIVE,
        ),
    ],
)
@pytest.mark.asyncio
async def test_identity_authoritative_binding_integrity_covers_complete_lifecycle(
    outcome, reason
):
    job = _job()
    provider = _provider(hospital_id=job.tenant_id, session="f" * 64)
    document = _document(job)
    candidate = SimpleNamespace(
        id=uuid.uuid4(),
        job_id=job.id,
        source_document_id=job.document_id,
        patient_id=job.patient_id,
        tenant_id=job.tenant_id,
    )
    route, decision = _route_decision(job)

    snapshots = [
        _authoritative_binding_snapshot(job, document, candidate, decision, route)
    ]
    db = _db(execute_results=[_scalar_result(None), _scalar_result(None)])
    with (
        patch(
            "app.services.identity_review._load_job", new=AsyncMock(return_value=job)
        ),
        patch("app.services.identity_review._authorize", new=AsyncMock()),
        patch(
            "app.services.identity_review._load_graph",
            new=AsyncMock(
                return_value=(
                    document,
                    [route],
                    [decision],
                    (IdentityReviewReasonCode.DOCUMENT_IDENTITY_MISMATCH,),
                )
            ),
        ),
        patch("app.services.identity_review.enqueue_audit_event", new=AsyncMock()),
    ):
        case = await create_case(
            db,
            job_id=job.id,
            provider=provider,
            capability_token="token",
            idempotency_key="binding-create-0001",
        )
    snapshots.append(
        _authoritative_binding_snapshot(job, document, candidate, decision, route)
    )

    with (
        patch(
            "app.services.identity_review._load_case", new=AsyncMock(return_value=case)
        ),
        patch("app.services.identity_review._authorize", new=AsyncMock()),
        patch(
            "app.services.identity_review._existing_operation",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.identity_review._revalidate_case_graph",
            new=AsyncMock(return_value=(job, document)),
        ),
        patch("app.services.identity_review.enqueue_audit_event", new=AsyncMock()),
    ):
        await claim_case(
            db,
            case_id=case.id,
            provider=provider,
            capability_token="token",
            expected_version=1,
            idempotency_key="binding-claim-0001",
        )
    snapshots.append(
        _authoritative_binding_snapshot(job, document, candidate, decision, route)
    )

    recovered_provider = provider.model_copy(update={"session_binding": "g" * 64})
    with (
        patch(
            "app.services.identity_review._load_case", new=AsyncMock(return_value=case)
        ),
        patch("app.services.identity_review._authorize", new=AsyncMock()),
        patch(
            "app.services.identity_review._existing_operation",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.identity_review._revalidate_case_graph",
            new=AsyncMock(return_value=(job, document)),
        ),
        patch("app.services.identity_review.enqueue_audit_event", new=AsyncMock()),
    ):
        await recover_session(
            db,
            case_id=case.id,
            provider=recovered_provider,
            capability_token="token",
            expected_version=2,
            idempotency_key="binding-recover-0001",
        )
    snapshots.append(
        _authoritative_binding_snapshot(job, document, candidate, decision, route)
    )

    with (
        patch(
            "app.services.identity_review._load_case", new=AsyncMock(return_value=case)
        ),
        patch("app.services.identity_review._authorize", new=AsyncMock()),
        patch(
            "app.services.identity_review._existing_operation",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.identity_review._revalidate_case_graph",
            new=AsyncMock(return_value=(job, document)),
        ),
        patch("app.services.identity_review.enqueue_audit_event", new=AsyncMock()),
    ):
        await submit_disposition(
            db,
            case_id=case.id,
            provider=recovered_provider,
            capability_token="token",
            expected_version=3,
            idempotency_key=f"binding-disposition-{outcome.value.lower()}",
            outcome=outcome,
            reason_codes=(reason,),
        )
    snapshots.append(
        _authoritative_binding_snapshot(job, document, candidate, decision, route)
    )

    baseline = snapshots[0]
    total_checked = (len(snapshots) - 1) * len(baseline)
    unchanged_bindings = sum(
        before == current
        for current in snapshots[1:]
        for (_, before), (_, current) in zip(baseline, current, strict=True)
    )
    identity_authoritative_binding_integrity = unchanged_bindings / total_checked
    assert identity_authoritative_binding_integrity == 1.0


@pytest.mark.asyncio
async def test_claim_versioning_allows_exactly_one_serialized_winner():
    job = _job()
    first = _provider(hospital_id=job.tenant_id, session="1" * 64)
    second = _provider(hospital_id=job.tenant_id, session="2" * 64)
    case = _case(first, job)
    db = _db()
    with (
        patch(
            "app.services.identity_review._load_case", new=AsyncMock(return_value=case)
        ),
        patch("app.services.identity_review._authorize", new=AsyncMock()),
        patch(
            "app.services.identity_review._existing_operation",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.identity_review._revalidate_case_graph",
            new=AsyncMock(return_value=(job, _document(job))),
        ),
        patch("app.services.identity_review.enqueue_audit_event", new=AsyncMock()),
    ):
        await claim_case(
            db,
            case_id=case.id,
            provider=first,
            capability_token="token-1",
            expected_version=1,
            idempotency_key="claim-winner-0001",
        )
        with pytest.raises(IdentityReviewError) as caught:
            await claim_case(
                db,
                case_id=case.id,
                provider=second,
                capability_token="token-2",
                expected_version=1,
                idempotency_key="claim-loser-0001",
            )
    assert caught.value.code == "IDENTITY_REVIEW_CASE_CONFLICT"
    assert case.assigned_reviewer_id == first.actor_uid


@pytest.mark.asyncio
async def test_disposition_versioning_allows_exactly_one_terminal_winner():
    job = _job()
    provider = _provider(hospital_id=job.tenant_id, session="3" * 64)
    case = _case(provider, job, status="IN_REVIEW", version=2, session="3" * 64)
    db = _db()
    with (
        patch(
            "app.services.identity_review._load_case", new=AsyncMock(return_value=case)
        ),
        patch("app.services.identity_review._authorize", new=AsyncMock()),
        patch(
            "app.services.identity_review._existing_operation",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.identity_review._revalidate_case_graph",
            new=AsyncMock(return_value=(job, _document(job))),
        ),
        patch("app.services.identity_review.enqueue_audit_event", new=AsyncMock()),
    ):
        await submit_disposition(
            db,
            case_id=case.id,
            provider=provider,
            capability_token="token",
            expected_version=2,
            idempotency_key="disposition-winner-0001",
            outcome=IdentityReviewOutcome.INSUFFICIENT_IDENTITY_EVIDENCE,
            reason_codes=(IdentityReviewReasonCode.IDENTITY_REVIEW_INCONCLUSIVE,),
        )
        with pytest.raises(IdentityReviewError) as caught:
            await submit_disposition(
                db,
                case_id=case.id,
                provider=provider,
                capability_token="token",
                expected_version=2,
                idempotency_key="disposition-loser-0001",
                outcome=IdentityReviewOutcome.INSUFFICIENT_IDENTITY_EVIDENCE,
                reason_codes=(IdentityReviewReasonCode.IDENTITY_REVIEW_INCONCLUSIVE,),
            )
    assert caught.value.code == "IDENTITY_REVIEW_CASE_ALREADY_RESOLVED"


@pytest.mark.asyncio
async def test_idempotency_replay_and_collision_are_durable_and_value_free():
    case_id = uuid.uuid4()
    existing = IdentityReviewOperationRecord(
        id=uuid.uuid4(),
        case_id=case_id,
        operation="CLAIM",
        actor_id=str(uuid.uuid4()),
        actor_role="identity_reviewer",
        idempotency_key="durable-key-0001",
        operation_hash="a" * 64,
        prior_version=1,
        result_version=2,
        created_at=datetime.now(timezone.utc),
    )
    same = _db(execute_results=[_scalar_result(existing)])
    replay = await _existing_operation(
        same,
        case_id=case_id,
        operation=SimpleNamespace(value="CLAIM"),
        idempotency_key="durable-key-0001",
        operation_hash="a" * 64,
    )
    assert replay is existing

    collision = _db(execute_results=[_scalar_result(existing)])
    with pytest.raises(IdentityReviewError) as caught:
        await _existing_operation(
            collision,
            case_id=case_id,
            operation=SimpleNamespace(value="CLAIM"),
            idempotency_key="durable-key-0001",
            operation_hash="b" * 64,
        )
    assert caught.value.code == "IDENTITY_REVIEW_IDEMPOTENCY_COLLISION"
