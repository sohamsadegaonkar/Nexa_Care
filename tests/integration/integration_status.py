"""Integration Status Report for WS1 Daily Runner.

Auto-generated summary of integration test results for Days 6-8
connected flow verification.
"""

INTEGRATION_STATUS = {
    "report_date": "2026-07-11",
    "workstream": "WS2/WS3/WS4/WS5 seam tests",
    "consent_flow": {
        "file": "tests/integration/test_consent_flow_qa.py",
        "total": 5,
        "passed": 5,
        "failed": 0,
        "tests": [
            {
                "name": "test_full_consent_flow_with_real_signatures",
                "status": "PASS",
                "description": "Enroll device → request consent → sign challenge → approve → validate → access record",
            },
            {
                "name": "test_denied_consent_flow_with_real_signatures",
                "status": "PASS",
                "description": "Denied decision → no consent token issued",
            },
            {
                "name": "test_wrong_key_signature_is_rejected",
                "status": "PASS",
                "description": "Enrolled key A, signed with key B → 401",
            },
            {
                "name": "test_consent_status_polling",
                "status": "PASS",
                "description": "Pending → approved transition via status polling",
            },
            {
                "name": "test_replayed_nonce_is_rejected",
                "status": "PASS",
                "description": "First approval succeeds, replay → 409",
            },
        ],
        "notes": "All signatures use REAL P-256 ECDSA keys (no bypasses).",
    },
    "pipeline_flow": {
        "file": "tests/integration/test_pipeline_flow_qa.py",
        "total": 8,
        "passed": 8,
        "failed": 0,
        "tests": [
            {
                "name": "test_auto_approved_pipeline_flow",
                "status": "PASS",
                "description": "Upload → all auto_approved → commit → verify committed",
            },
            {
                "name": "test_needs_review_pipeline_flow",
                "status": "PASS",
                "description": "Upload → needs_review → review → approve → commit",
            },
            {
                "name": "test_rejected_field_excluded_from_commit",
                "status": "PASS",
                "description": "Review rejects field → commit only includes approved",
            },
            {
                "name": "test_edited_field_in_commit",
                "status": "PASS",
                "description": "Review → edit field → commit includes corrected value",
            },
            {
                "name": "test_commit_rejects_spoofed_patient_id",
                "status": "PASS",
                "description": "Wrong patient_id in payload → 400 (server-side derivation)",
            },
            {
                "name": "test_nonexistent_job_returns_404",
                "status": "PASS",
                "description": "Job not found → 404 before consent check",
            },
            {
                "name": "test_commit_requires_confidence_and_risk_level",
                "status": "PASS",
                "description": "Fields without confidence/risk_level rejected at commit",
            },
            {
                "name": "test_full_flow_with_review_queue",
                "status": "PASS",
                "description": "Upload → review queue → adjudicate → commit",
            },
        ],
        "notes": "Pipeline routes derive patient_id server-side from DB entities (spoofing-safe).",
    },
    "unit_tests": {
        "test_pipeline_qa": {"total": 33, "passed": 33, "failed": 0},
        "test_pipeline_consent_server_side": {"total": 22, "passed": 22, "failed": 0},
    },
    "lint": {
        "tool": "ruff",
        "result": "All checks passed",
        "violations": 0,
    },
    "fixes_applied": [
        "Replaced all list-based mock_db.execute.side_effect with _side_effect_with_fallback() to prevent StopAsyncIteration from extra DB calls",
        "Added _reset_mock_db() between HTTP requests (explicitly clears side_effect before reset_mock)",
        "Added _db_result() convenience factory for readable mock DB results",
        "Fixed ingest_extracted_fields mock patch: must patch at usage site (app.api.v2.pipeline_routes) not definition site, because route does from-import",
        "Moved ingest_extracted_fields patch into _consent_and_audit_patches() stack so all pipeline tests get it automatically",
        "Removed debug call_count_before variable from test_auto_approved_pipeline_flow",
        "Added _reset_mock_db() calls before each HTTP request in consent flow tests for isolation",
    ],
}
