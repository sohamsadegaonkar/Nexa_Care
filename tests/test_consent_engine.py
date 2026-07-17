"""Tests for ConsentEngine -- the Phase 1 replacement for consent_service.py
and app/services/consent/routine.py, with break_glass folded in.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from app.services.consent_engine import (
    ConsentEngineUnavailable,
    consume,
    issue,
    revoke,
    validate,
)
from app.models.assurance import AssuranceLevel


def run(coro):
    return asyncio.run(coro)


class FakeConsentDB:
    """Minimal AsyncSession double: tracks add/commit/rollback and answers
    a single select-by-token_hash query with a preset row (or None).
    """

    def __init__(self, existing_row=None, commit_error=None, events=None):
        self.added = []
        self.commit_count = 0
        self.rolled_back = False
        self.commit_error = commit_error
        self.existing_row = existing_row
        self.events = events if events is not None else []

    def add(self, obj):
        self.added.append(obj)
        self.events.append("DB_ADD")

    async def commit(self):
        self.events.append("DB_COMMIT")
        if self.commit_error is not None:
            err, self.commit_error = self.commit_error, None  # only raise once
            raise err
        self.commit_count += 1

    async def rollback(self):
        self.rolled_back = True
        self.events.append("DB_ROLLBACK")

    async def execute(self, stmt):
        self.events.append("DB_EXECUTE")
        row = self.existing_row if self.existing_row is not None else (self.added[-1] if self.added else None)
        return _FakeResult(row)


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


def make_audit_503_mock(events):
    async def _mock(*, actor_uid, event_type, target_id, status, metadata=None, event_timestamp=None):
        events.append(f"AUDIT_503:{event_type}")
    return AsyncMock(side_effect=_mock)


class TestIssue(unittest.TestCase):
    @patch("app.services.consent_engine.append_audit_log", new_callable=AsyncMock)
    @patch("app.services.consent_engine.append_audit_log_or_503", new_callable=AsyncMock)
    @patch("app.services.consent_engine.get_consent_redis_client")
    def test_issue_writes_postgres_then_redis_and_audits_in_order(self, mock_get_client, mock_audit_503, mock_audit):
        events: list[str] = []
        mock_audit_503.side_effect = make_audit_503_mock(events).side_effect
        redis_client = AsyncMock()
        mock_get_client.return_value = redis_client
        db = FakeConsentDB(events=events)

        token = run(issue(
            db=db,
            patient_id="patient-1",
            clinician_id="clinician-1",
            purpose="routine",
            scope=["clinical"],
            assurance_level=AssuranceLevel.STANDARD,
            assurance_evidence={},
        ))

        self.assertTrue(token)
        self.assertEqual(len(db.added), 1)
        redis_client.set.assert_awaited_once()
        # ATTEMPT audited before any write; SUCCESS audited only after
        # both the Postgres commit and the Redis write land.
        self.assertEqual(
            events,
            ["AUDIT_503:CONSENT_GRANT_ATTEMPT", "DB_ADD", "DB_COMMIT", "AUDIT_503:CONSENT_GRANT_SUCCESS"],
        )

    @patch("app.services.consent_engine.append_audit_log", new_callable=AsyncMock)
    @patch("app.services.consent_engine.append_audit_log_or_503", new_callable=AsyncMock)
    def test_empty_scope_raises_before_any_audit_or_write(self, mock_audit_503, mock_audit):
        db = FakeConsentDB()

        with self.assertRaises(ValueError):
            run(issue(
                db=db, patient_id="p", clinician_id="c", purpose="routine",
                scope=[], assurance_level=AssuranceLevel.STANDARD, assurance_evidence={}
            ))

        mock_audit_503.assert_not_awaited()
        self.assertEqual(len(db.added), 0)

    @patch("app.services.consent_engine.append_audit_log", new_callable=AsyncMock)
    @patch("app.services.consent_engine.append_audit_log_or_503", new_callable=AsyncMock)
    def test_break_glass_without_reason_code_raises_before_any_write(self, mock_audit_503, mock_audit):
        db = FakeConsentDB()

        with self.assertRaises(ValueError):
            run(issue(
                db=db, patient_id="p", clinician_id="c", purpose="emergency",
                scope=["clinical"], is_break_glass=True,
                assurance_level=AssuranceLevel.BREAK_GLASS, assurance_evidence={},
            ))

        mock_audit_503.assert_not_awaited()
        self.assertEqual(len(db.added), 0)

    @patch("app.services.consent_engine.append_audit_log", new_callable=AsyncMock)
    @patch("app.services.consent_engine.append_audit_log_or_503", new_callable=AsyncMock)
    @patch("app.services.consent_engine.get_consent_redis_client")
    def test_break_glass_pushes_to_compliance_queue(self, mock_get_client, mock_audit_503, mock_audit):
        redis_client = AsyncMock()
        mock_get_client.return_value = redis_client
        db = FakeConsentDB()

        run(issue(
            db=db, patient_id="p", clinician_id="c", purpose="emergency",
            scope=["clinical"], is_break_glass=True, reason_code="unconscious patient, ICU",
            assurance_level=AssuranceLevel.BREAK_GLASS, assurance_evidence={},
        ))

        redis_client.rpush.assert_awaited_once()
        queue_key, payload_json = redis_client.rpush.await_args.args
        self.assertEqual(queue_key, "nexa:compliance_queue:break_glass")
        payload = json.loads(payload_json)
        self.assertEqual(payload["reason_code"], "unconscious patient, ICU")

    @patch("app.services.consent_engine.append_audit_log", new_callable=AsyncMock)
    @patch("app.services.consent_engine.append_audit_log_or_503", new_callable=AsyncMock)
    def test_postgres_write_failure_rolls_back_and_raises(self, mock_audit_503, mock_audit):
        db = FakeConsentDB(commit_error=RuntimeError("db down"))

        with self.assertRaises(ConsentEngineUnavailable):
            run(issue(
                db=db, patient_id="p", clinician_id="c", purpose="routine",
                scope=["clinical"], assurance_level=AssuranceLevel.STANDARD, assurance_evidence={}
            ))

        self.assertTrue(db.rolled_back)
        self.assertEqual(mock_audit.await_args.kwargs["event_type"], "CONSENT_GRANT_FAILED")
        self.assertEqual(mock_audit.await_args.kwargs["status"], "DURABLE_LOG_WRITE_FAILED")

    @patch("app.services.consent_engine.append_audit_log", new_callable=AsyncMock)
    @patch("app.services.consent_engine.append_audit_log_or_503", new_callable=AsyncMock)
    @patch("app.services.consent_engine.get_consent_redis_client")
    def test_redis_failure_after_postgres_commit_marks_row_revoked_not_a_noop_rollback(
        self, mock_get_client, mock_audit_503, mock_audit
    ):
        """Regression test for the two-phase-write gap: db.rollback() is
        a no-op once db.commit() already succeeded, so a Redis failure
        after that point must be handled by explicitly revoking the
        just-committed row -- not by pretending rollback() undid it.
        """
        redis_client = AsyncMock()
        redis_client.set.side_effect = RuntimeError("redis down")
        mock_get_client.return_value = redis_client
        db = FakeConsentDB()

        with self.assertRaises(ConsentEngineUnavailable):
            run(issue(
                db=db, patient_id="p", clinician_id="c", purpose="routine",
                scope=["clinical"], assurance_level=AssuranceLevel.STANDARD, assurance_evidence={}
            ))

        self.assertEqual(len(db.added), 1)
        row = db.added[0]
        self.assertIsNotNone(row.revoked_at)
        self.assertEqual(row.revoked_reason, "redis_write_failed")
        self.assertEqual(db.commit_count, 2)  # initial issue commit + revoke commit
        self.assertEqual(mock_audit.await_args.kwargs["status"], "LIVE_STORE_WRITE_FAILED")


class TestValidate(unittest.TestCase):
    @patch("app.services.consent_engine.get_consent_redis_client")
    def test_matching_live_token_returns_capability(self, mock_get_client):
        redis_client = AsyncMock()
        redis_client.get.return_value = json.dumps({
            "patient_id": "p", "clinician_id": "c", "purpose": "routine",
            "scope": ["clinical"], "is_break_glass": False, "reason_code": None,
            "issued_at": "2026-07-03T00:00:00+00:00",
            "expires_at": "2026-07-03T01:00:00+00:00",
        })
        mock_get_client.return_value = redis_client

        capability = run(validate(token="tok", patient_id="p", clinician_id="c", purpose="routine"))

        self.assertIsNotNone(capability)
        self.assertEqual(capability.scope, ["clinical"])

    @patch("app.services.consent_engine.get_consent_redis_client")
    def test_mismatched_clinician_fails_closed(self, mock_get_client):
        redis_client = AsyncMock()
        redis_client.get.return_value = json.dumps({
            "patient_id": "p", "clinician_id": "someone-else", "purpose": "routine",
            "scope": ["clinical"], "is_break_glass": False, "reason_code": None,
            "issued_at": "2026-07-03T00:00:00+00:00",
            "expires_at": "2026-07-03T01:00:00+00:00",
        })
        mock_get_client.return_value = redis_client

        capability = run(validate(token="tok", patient_id="p", clinician_id="c", purpose="routine"))

        self.assertIsNone(capability)

    @patch("app.services.consent_engine.get_consent_redis_client")
    def test_missing_token_fails_closed(self, mock_get_client):
        redis_client = AsyncMock()
        redis_client.get.return_value = None
        mock_get_client.return_value = redis_client

        self.assertIsNone(run(validate(token="tok", patient_id="p", clinician_id="c", purpose="routine")))

    @patch("app.services.consent_engine.get_consent_redis_client")
    def test_redis_error_raises_unavailable_not_silently_false(self, mock_get_client):
        redis_client = AsyncMock()
        redis_client.get.side_effect = RuntimeError("redis down")
        mock_get_client.return_value = redis_client

        with self.assertRaises(ConsentEngineUnavailable):
            run(validate(token="tok", patient_id="p", clinician_id="c", purpose="routine"))

    @patch("app.services.consent_engine.get_consent_redis_client")
    def test_break_glass_payload_without_reason_code_is_rejected_as_malformed(self, mock_get_client):
        redis_client = AsyncMock()
        redis_client.get.return_value = json.dumps({
            "patient_id": "p", "clinician_id": "c", "purpose": "emergency",
            "scope": ["clinical"], "is_break_glass": True, "reason_code": None,
            "issued_at": "2026-07-03T00:00:00+00:00",
            "expires_at": "2026-07-03T00:15:00+00:00",
        })
        mock_get_client.return_value = redis_client

        self.assertIsNone(run(validate(token="tok", patient_id="p", clinician_id="c", purpose="emergency")))


class TestConsume(unittest.TestCase):
    @patch("app.services.consent_engine.append_audit_log", new_callable=AsyncMock)
    @patch("app.services.consent_engine.get_consent_redis_client")
    def test_consume_deletes_from_redis_and_marks_postgres_row_consumed(self, mock_get_client, mock_audit):
        redis_client = AsyncMock()
        redis_client.getdel.return_value = json.dumps({
            "patient_id": "p", "clinician_id": "c", "purpose": "routine",
            "scope": ["clinical"], "is_break_glass": False, "reason_code": None,
            "issued_at": "2026-07-03T00:00:00+00:00",
            "expires_at": "2026-07-03T01:00:00+00:00",
        })
        mock_get_client.return_value = redis_client

        class _Row:
            consumed_at = None

        row = _Row()
        db = FakeConsentDB(existing_row=row)

        capability = run(consume(db=db, token="tok", patient_id="p", clinician_id="c", purpose="routine"))

        self.assertIsNotNone(capability)
        self.assertIsNotNone(row.consumed_at)
        redis_client.getdel.assert_awaited_once()

    @patch("app.services.consent_engine.append_audit_log", new_callable=AsyncMock)
    @patch("app.services.consent_engine.get_consent_redis_client")
    def test_consume_mismatch_returns_none_without_touching_postgres(self, mock_get_client, mock_audit):
        redis_client = AsyncMock()
        redis_client.getdel.return_value = json.dumps({
            "patient_id": "someone-else", "clinician_id": "c", "purpose": "routine",
            "scope": ["clinical"], "is_break_glass": False, "reason_code": None,
            "issued_at": "2026-07-03T00:00:00+00:00",
            "expires_at": "2026-07-03T01:00:00+00:00",
        })
        mock_get_client.return_value = redis_client
        db = FakeConsentDB()

        capability = run(consume(db=db, token="tok", patient_id="p", clinician_id="c", purpose="routine"))

        self.assertIsNone(capability)
        self.assertEqual(db.commit_count, 0)

    @patch("app.services.consent_engine.get_consent_redis_client")
    def test_consume_redis_error_raises_unavailable(self, mock_get_client):
        redis_client = AsyncMock()
        redis_client.getdel.side_effect = RuntimeError("redis down")
        mock_get_client.return_value = redis_client
        db = FakeConsentDB()

        with self.assertRaises(ConsentEngineUnavailable):
            run(consume(db=db, token="tok", patient_id="p", clinician_id="c", purpose="routine"))


class TestRevoke(unittest.TestCase):
    @patch("app.services.consent_engine.get_consent_redis_client")
    def test_revoke_never_raises_even_if_both_stores_fail(self, mock_get_client):
        redis_client = AsyncMock()
        redis_client.delete.side_effect = RuntimeError("redis down")
        mock_get_client.return_value = redis_client

        class _BrokenDB(FakeConsentDB):
            async def execute(self, stmt):
                raise RuntimeError("db down")

        db = _BrokenDB()

        # Must not raise.
        run(revoke(db=db, token="tok"))

    @patch("app.services.consent_engine.get_consent_redis_client")
    def test_revoke_marks_unconsumed_row_revoked(self, mock_get_client):
        redis_client = AsyncMock()
        mock_get_client.return_value = redis_client

        class _Row:
            revoked_at = None
            revoked_reason = None
            consumed_at = None

        row = _Row()
        db = FakeConsentDB(existing_row=row)

        run(revoke(db=db, token="tok", reason="provider requested"))

        self.assertIsNotNone(row.revoked_at)
        self.assertEqual(row.revoked_reason, "provider requested")

    @patch("app.services.consent_engine.get_consent_redis_client")
    def test_revoke_does_not_overwrite_an_already_consumed_row(self, mock_get_client):
        redis_client = AsyncMock()
        mock_get_client.return_value = redis_client

        class _Row:
            revoked_at = None
            revoked_reason = None
            consumed_at = "already-consumed"

        row = _Row()
        db = FakeConsentDB(existing_row=row)

        run(revoke(db=db, token="tok"))

        self.assertIsNone(row.revoked_at)


if __name__ == "__main__":
    unittest.main()
