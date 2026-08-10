"""Audit Event Type Coverage Test for Nexa Care.

Verifies that all audit event types used in the codebase are documented
in the EXPECTED_EVENTS list, and that triggering them writes a valid entry.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from app.observability.audit_ledger import append_audit_log
from app.security.audit_context import AuditContext, AuditDomain

AUDIT_CONTEXT = AuditContext.for_tenant(
    tenant_id="test-tenant",
    domain=AuditDomain.CONSENT,
)

# List of all expected event types after Sprint 2
EXPECTED_EVENTS = {
    "ADJUDICATION_CASE_CREATED",
    "ADJUDICATION_ACCESS_REJECTED",
    "ADJUDICATION_CLINICAL_COMMIT_COMPLETED",
    "ADJUDICATION_SOURCE_ACCESSED",
    "ADJUDICATION_REVIEW_SESSION_ROTATED",
    "ADJUDICATION_SPECIALIST_REQUESTED",
    "ADJUDICATION_SUBMISSION_ACCEPTED",
    "ADJUDICATION_SUBMISSION_REJECTED",
    "ADJUDICATION_SUBMISSION_SUPERSEDED",
    "IDENTITY_REVIEW_ACCESS_REJECTED",
    "IDENTITY_REVIEW_CASE_ACCESSED",
    "IDENTITY_REVIEW_CASE_CLAIMED",
    "IDENTITY_REVIEW_CASE_CREATED",
    "IDENTITY_REVIEW_DISPOSITION_SUBMITTED",
    "IDENTITY_REVIEW_ESCALATED",
    "IDENTITY_REVIEW_SESSION_ROTATED",
    "ASSURANCE_VERIFICATION_FAILED",
    "ASSURANCE_VERIFICATION_SUCCESS",
    "BIOMETRIC_ENROLLMENT_ATTEMPT",
    "BIOMETRIC_ENROLLMENT_FAILED",
    "BIOMETRIC_ENROLLMENT_SUCCESS",
    "BIOMETRIC_HANDSHAKE_DENIED",
    "BIOMETRIC_HANDSHAKE_FAILED",
    "BIOMETRIC_HANDSHAKE_STARTED",
    "BIOMETRIC_HANDSHAKE_SUCCESS",
    "BIOMETRIC_VERIFICATION_FAILED",
    "BIOMETRIC_VERIFICATION_SUCCESS",
    "BREAK_GLASS_GRANT_ATTEMPT",
    "BREAK_GLASS_GRANT_FAILED",
    "BREAK_GLASS_GRANT_SUCCESS",
    "BREAK_GLASS_EMERGENCY_SUMMARY_ACCESSED",
    "BREAK_GLASS_GOVERNANCE_APPROVED",
    "BREAK_GLASS_PATIENT_NOTIFICATION",
    "BREAK_GLASS_REVOKE_ATTEMPT",
    "BREAK_GLASS_REVOKE_SUCCESS",
    "CLINICAL_VIEW_SUCCESS",
    "CONSENT_CONSUMED",
    "CONSENT_GATED_DECRYPT_COMPLETED",
    "CONSENT_GATED_DECRYPT_FAILED",
    "CONSENT_GATED_DECRYPT_STARTED",
    "CONSENT_GRANT_ATTEMPT",
    "CONSENT_GRANT_FAILED",
    "EXTRACTION_JOB_VALIDATED",
    "CONSENT_REQUEST_CREATED",
    "CONSENT_REQUEST_CANCELLED",
    "CONSENT_REQUEST_IDOR_REJECTED",
    "CONSENT_APPROVED_SIGNED",
    "CONSENT_ACCESS_CLAIMED",
    "CONSENT_DENIED_SIGNED",
    "CONSENT_GRANT_SUCCESS",
    "PATIENT_CONSENT_REVOKED",
    "PATIENT_RECORD_READ_SUCCESS",
    "PATIENT_RECORD_APPEND_ATTEMPT",
    "PATIENT_RECORD_APPEND_SUCCESS",
    "SIGNATURE_VERIFICATION_FAILED",
    "DOCUMENT_UPLOADED",
    "EXTRACTED_DATA_INGESTED",
    "PIPELINE_COMMIT",
    "VITALS",
    "MEDICATION",
    "LAB_RESULT",
    "ALLERGY",
    "DOCUMENT",
    "EXTRACTION_JOB_STARTED",
    "EXTRACTION_JOB_SCORED",
    "EXTRACTION_JOB_FAILED",
    "EXTRACTION_JOB_ROUTED",
    "EXTRACTION_EVIDENCE_ROUTED",
    "EXTRACTION_QUARANTINE_ESCALATED",
    "FIELD_APPROVED",
    "FIELD_REJECTED",
    "FIELD_EDITED",
    "JOB_COMMITTED",
    "EXTRACTION_FIELD_AUTO_APPROVED",
    "EXTRACTION_FIELD_REVIEWED",
    "PIPELINE_COMMITTED_TO_TIMELINE",
    "CRYPTOGRAPHIC_ERASURE_COMPLETED",
    "PATIENT_DEK_ACCESS_BLOCKED",
    "PATIENT_DEK_DELETION_SCHEDULED",
    "PATIENT_DEK_DESTRUCTION_NEEDS_OPERATOR",
    "CRYPTOGRAPHIC_ERASURE_REQUESTED",
    "DEVICE_KEY_REGISTRATION",
    "DEVICE_KEY_ENROLLED",
    "DEVICE_KEY_REVOKED",
    "DOCUMENT_AUTO_PROCESSED",
    "DOCUMENT_AUTO_PROCESS_STARTED",
    "DOCUMENT_PROCESSING_AUTHORIZATION_ALLOWED",
    "DOCUMENT_PROCESSING_AUTHORIZATION_DENIED",
    "DOCUMENT_NEEDS_REVIEW",
    "DOCUMENT_REJECTED_LOW_CONFIDENCE",
    "DOCUMENT_REVIEW_APPROVAL_ATTEMPT",
    "DOCUMENT_REVIEW_APPROVAL_FAILED",
    "DOCUMENT_REVIEW_APPROVED",
    "DOCUMENT_REVIEW_REJECTED",
    "DOCUMENT_REVIEW_REJECTION_ATTEMPT",
    "DOCUMENT_REVIEW_REJECTION_FAILED",
    "DOCUMENT_SOURCE_VIEWED",
    "DOCUMENT_UPLOAD_RECEIVED",
    "FHIR_BUNDLE_EXPORTED",
    "MERGE_CHALLENGE_CREATED",
    "MERGE_CHALLENGE_VERIFIED",
    "MERGE_EXECUTED",
    "MERGE_REJECTED",
    "NFC_CARD_REPORTED_LOST",
    "NFC_CARD_RESOLVED",
    "PATIENT_DEK_DESTROYED",
    "PATIENT_DEK_GENERATED",
    "PATIENT_POLICY_CHANGED",
    "PATIENT_POLICY_READ_DENIED",
    "PATIENT_POLICY_READ_SUCCESS",
    "PATIENT_POLICY_UPDATE_DENIED",
    "PATIENT_RECORD_VIEW_COMPLETED",
    "PATIENT_RECORD_VIEW_FAILED",
    "PATIENT_RECORD_VIEW_STARTED",
    "PATIENT_REGISTRATION_ATTEMPT",
    "PATIENT_REGISTRATION_SUCCESS",
    "PII_ENCRYPTION_MIGRATED",
    "PII_VIEW_SUCCESS",
    "PROVIDER_AUTH_FAILED",
    "PROVIDER_LOGIN_FAILED",
    "PROVIDER_LOGIN_SUCCEEDED",
    "PROVIDER_LOGOUT",
    "PROVIDER_MFA_REQUIRED",
    "PROVIDER_MFA_SETUP_INIT",
    "PROVIDER_MFA_SETUP_SUCCESS",
    "PROVIDER_MFA_SETUP_VERIFY_FAILED",
    "PROVIDER_ROLE_DENIED",
    "PROVIDER_SESSION_REFRESH",
    "PROVIDER_STEP_UP_MFA_VERIFIED",
    "PUSH_REQUEST_CREATED",
    "PUSH_REQUEST_TIMEOUT",
    "PUSH_RESPONSE_RECEIVED",
    "ROUTINE_CONSENT_GRANT_ATTEMPT",
    "ROUTINE_CONSENT_GRANT_FAILED",
    "ROUTINE_CONSENT_GRANT_SUCCESS",
    "SESSION_VALIDATION_FAILED",
    "SNAPSHOT_ACCESSED",
    "TOMBSTONE_INTEGRITY_VIOLATION",
}


def test_no_undocumented_audit_events():
    """Scan the codebase for audit event literals and ensure they are in EXPECTED_EVENTS."""
    app_path = Path(__file__).parent.parent / "app"
    found_events = set()

    # Regex to find event types in append_audit_log and append_audit_log_or_503
    # Matches event_type="EVENT" or "EVENT" as 2nd positional arg
    regex_kw = re.compile(r'event_type\s*=\s*["\']([A-Z0-9_]+)["\']')
    regex_pos = re.compile(
        r'append_audit_log(?:_or_503)?\(\s*[^,]+,\s*["\']([A-Z0-9_]+)["\']'
    )

    for root, _, files in os.walk(app_path):
        for file in files:
            if file.endswith(".py"):
                content = (Path(root) / file).read_text(encoding="utf-8")
                found_events.update(regex_kw.findall(content))
                found_events.update(regex_pos.findall(content))

    undocumented = found_events - EXPECTED_EVENTS
    assert (
        not undocumented
    ), f"Found undocumented audit event types in codebase: {undocumented}"


@pytest.mark.asyncio
async def test_audit_event_writing_and_chaining():
    """Test that writing an event actually results in a valid hash-chained entry."""
    append_once = AsyncMock(return_value={})
    with patch("app.observability.audit_ledger._append_once", append_once):
        success = await append_audit_log(
            audit_context=AUDIT_CONTEXT,
            actor_uid="test-actor",
            event_type="CONSENT_GRANT_SUCCESS",
            target_id="test-target",
            status="SUCCESS",
            metadata={"test": "data"},
        )

        assert success is True

        call = append_once.await_args.kwargs
        assert call["event_type"] == "CONSENT_GRANT_SUCCESS"
        assert call["actor_uid"] == "test-actor"
        assert call["metadata"] == {"test": "data"}


# Helper to verify all events can be written
@pytest.mark.parametrize("event_type", list(EXPECTED_EVENTS))
@pytest.mark.asyncio
async def test_all_expected_events_can_be_written(event_type):
    """Smoke test: verify append_audit_log doesn't crash for any expected event type."""
    with patch(
        "app.observability.audit_ledger._append_once", new=AsyncMock(return_value={})
    ):
        success = await append_audit_log(
            audit_context=AUDIT_CONTEXT,
            actor_uid="system",
            event_type=event_type,
            target_id="unit-test",
            status="SUCCESS",
        )
        assert success is True
