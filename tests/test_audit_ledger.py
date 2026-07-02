import asyncio
import unittest
from unittest.mock import patch, MagicMock

from fastapi import HTTPException

from app.observability.audit_ledger import _calculate_hash, append_audit_log, append_audit_log_or_503
from app.core.request_context import trace_id_var


def run(coro):
    return asyncio.run(coro)


class FakeResult:
    """Mimics a successful postgrest APIResponse. Only used for return
    values — errors are modeled as raised exceptions (see FakeAPIError),
    matching how postgrest==0.17.2's execute() actually behaves (verified
    against the real source: it raises APIError on any non-2xx response,
    it does not return an object with a populated .error attribute).
    """
    def __init__(self, data=None):
        self.data = data


class FakeAPIError(Exception):
    """Stand-in for postgrest.exceptions.APIError. Exposes the same
    `.code` attribute the real class sets from PostgREST's JSON error
    body, without importing postgrest directly (it's a transitive
    dependency of supabase-py, not declared in requirements.txt).
    """
    def __init__(self, code, message="error"):
        self.code = code
        super().__init__(message)


def make_fake_supabase(execute_side_effect):
    """Builds a fake supabase client matching the fluent
    .table(...).select(...).order(...).limit(...).execute() /
    .table(...).insert(...).execute() call chains used by audit_ledger.

    execute_side_effect: list where each item is either a FakeResult
    (return value) or an Exception instance (raised). Consumed in order,
    one per .execute() call, across both reads and inserts.
    """
    mock_table = MagicMock()
    mock_table.select.return_value = mock_table
    mock_table.order.return_value = mock_table
    mock_table.limit.return_value = mock_table
    mock_table.insert.return_value = mock_table
    mock_table.execute.side_effect = execute_side_effect

    mock_client = MagicMock()
    mock_client.table.return_value = mock_table
    return mock_client, mock_table


class TestCalculateHash(unittest.TestCase):

    def test_same_inputs_produce_same_hash(self):
        payload = {"event": "X", "status": "OK"}
        h1 = _calculate_hash(payload, "GENESIS")
        h2 = _calculate_hash(payload, "GENESIS")
        self.assertEqual(h1, h2)

    def test_different_payload_changes_hash(self):
        h1 = _calculate_hash({"event": "X"}, "GENESIS")
        h2 = _calculate_hash({"event": "Y"}, "GENESIS")
        self.assertNotEqual(h1, h2)

    def test_different_previous_hash_changes_hash(self):
        payload = {"event": "X"}
        h1 = _calculate_hash(payload, "GENESIS")
        h2 = _calculate_hash(payload, "some-other-previous-hash")
        self.assertNotEqual(h1, h2)

    def test_hash_is_sha256_hexdigest(self):
        h = _calculate_hash({"event": "X"}, "GENESIS")
        self.assertEqual(len(h), 64)
        int(h, 16)  # raises ValueError if not valid hex


class TestAppendAuditLog(unittest.TestCase):

    def setUp(self):
        self._token = trace_id_var.set("trace-test123")

    def tearDown(self):
        trace_id_var.reset(self._token)

    @patch("app.observability.audit_ledger.get_supabase_client")
    def test_success_links_to_previous_hash_and_threads_trace_id(self, mock_get_client):
        select_result = FakeResult(data=[{"record_hash": "prev-hash-abc"}])
        insert_result = FakeResult(data=[{"ok": True}])
        mock_client, mock_table = make_fake_supabase([select_result, insert_result])
        mock_get_client.return_value = mock_client

        with self.assertNoLogs("nexa_logger", level="CRITICAL"):
            result = run(append_audit_log(
                actor_uid="TEST_ACTOR",
                event_type="TEST_EVENT",
                target_id="target-1",
                status="SUCCESS",
            ))

        self.assertTrue(result)

        inserted_row = mock_table.insert.call_args[0][0]
        self.assertEqual(inserted_row["previous_hash"], "prev-hash-abc")
        self.assertEqual(inserted_row["trace_id"], "trace-test123")
        self.assertEqual(inserted_row["payload"]["trace_id"], "trace-test123")

        expected_hash = _calculate_hash(inserted_row["payload"], "prev-hash-abc")
        self.assertEqual(inserted_row["record_hash"], expected_hash)

    @patch("app.observability.audit_ledger.get_supabase_client")
    def test_no_prior_rows_falls_back_to_genesis(self, mock_get_client):
        select_result = FakeResult(data=[])
        insert_result = FakeResult(data=[{"ok": True}])
        mock_client, mock_table = make_fake_supabase([select_result, insert_result])
        mock_get_client.return_value = mock_client

        result = run(append_audit_log("ACTOR", "EVENT", "target-1", "SUCCESS"))

        self.assertTrue(result)
        inserted_row = mock_table.insert.call_args[0][0]
        self.assertEqual(inserted_row["previous_hash"], "GENESIS")

    @patch("app.observability.audit_ledger.get_supabase_client")
    def test_read_error_returns_false_and_logs_critical(self, mock_get_client):
        mock_client, mock_table = make_fake_supabase([ConnectionError("connection reset")])
        mock_get_client.return_value = mock_client

        with self.assertLogs("nexa_logger", level="CRITICAL") as cm:
            result = run(append_audit_log("ACTOR", "EVENT", "target-1", "SUCCESS"))

        self.assertFalse(result)
        self.assertIn("audit_log_write_failed", cm.output[0])
        self.assertIn("could_not_read_previous_hash", cm.output[0])
        # Read failed, so we must never attempt the insert.
        mock_table.insert.assert_not_called()

    @patch("app.observability.audit_ledger.get_supabase_client")
    def test_non_collision_insert_error_returns_false_without_retrying(self, mock_get_client):
        select_result = FakeResult(data=[])
        insert_error = FakeAPIError(code="23514", message="check constraint violated")
        mock_client, mock_table = make_fake_supabase([select_result, insert_error])
        mock_get_client.return_value = mock_client

        with self.assertLogs("nexa_logger", level="CRITICAL") as cm:
            result = run(append_audit_log("ACTOR", "EVENT", "target-1", "SUCCESS"))

        self.assertFalse(result)
        self.assertIn("audit_log_write_failed", cm.output[0])
        # A non-collision error must NOT retry — exactly one read + one insert.
        self.assertEqual(mock_table.execute.call_count, 2)

    @patch("app.observability.audit_ledger.get_supabase_client")
    def test_unexpected_exception_returns_false_and_logs_critical(self, mock_get_client):
        mock_get_client.side_effect = ConnectionError("supabase unreachable")

        with self.assertLogs("nexa_logger", level="CRITICAL") as cm:
            result = run(append_audit_log("ACTOR", "EVENT", "target-1", "SUCCESS"))

        self.assertFalse(result)
        self.assertIn("audit_log_write_failed", cm.output[0])

    # ── F-05 retry behavior: the actual bug being fixed ────────────────────

    @patch("app.observability.audit_ledger.get_supabase_client")
    def test_unique_violation_retries_and_succeeds(self, mock_get_client):
        """Two writers race on the same previous_hash. First insert attempt
        hits the UNIQUE(previous_hash) constraint (23505); the code must
        re-read the now-current latest hash and retry, not give up.
        """
        select_1 = FakeResult(data=[{"record_hash": "hash-A"}])
        collision = FakeAPIError(code="23505", message="duplicate key value violates unique constraint")
        select_2 = FakeResult(data=[{"record_hash": "hash-B"}])  # the other writer's row, now visible
        insert_success = FakeResult(data=[{"ok": True}])

        mock_client, mock_table = make_fake_supabase(
            [select_1, collision, select_2, insert_success]
        )
        mock_get_client.return_value = mock_client

        with self.assertLogs("nexa_logger", level="WARNING") as cm:
            result = run(append_audit_log("ACTOR", "EVENT", "target-1", "SUCCESS"))

        self.assertTrue(result)
        self.assertIn("audit_log_hash_collision", cm.output[0])

        # Must have retried against the freshly re-read hash, not the stale one.
        final_inserted_row = mock_table.insert.call_args[0][0]
        self.assertEqual(final_inserted_row["previous_hash"], "hash-B")
        self.assertEqual(mock_table.execute.call_count, 4)

    @patch("app.observability.audit_ledger.get_supabase_client")
    def test_unique_violation_exhausts_retries_returns_false(self, mock_get_client):
        collision = FakeAPIError(code="23505", message="duplicate key value violates unique constraint")
        select_result = FakeResult(data=[{"record_hash": "hash-A"}])

        # 5 attempts: read, insert(collision) repeated 5 times = 10 execute() calls.
        side_effect = [select_result, collision] * 5
        mock_client, mock_table = make_fake_supabase(side_effect)
        mock_get_client.return_value = mock_client

        with self.assertLogs("nexa_logger", level="CRITICAL") as cm:
            result = run(append_audit_log("ACTOR", "EVENT", "target-1", "SUCCESS"))

        self.assertFalse(result)
        self.assertIn("unique_violation_max_retries_exceeded", cm.output[-1])
        self.assertEqual(mock_table.execute.call_count, 10)


class TestAppendAuditLogOr503(unittest.TestCase):

    @patch("app.observability.audit_ledger.append_audit_log")
    def test_success_returns_none_does_not_raise(self, mock_append):
        mock_append.return_value = True

        result = run(append_audit_log_or_503(
            actor_uid="TEST", event_type="EVENT", target_id="t1", status="OK",
        ))

        self.assertIsNone(result)

    @patch("app.observability.audit_ledger.append_audit_log")
    def test_failure_raises_http_503(self, mock_append):
        mock_append.return_value = False

        with self.assertRaises(HTTPException) as cm:
            run(append_audit_log_or_503(
                actor_uid="TEST", event_type="EVENT", target_id="t1", status="OK",
            ))

        self.assertEqual(cm.exception.status_code, 503)

    @patch("app.observability.audit_ledger.append_audit_log")
    def test_forwards_all_arguments_unchanged(self, mock_append):
        mock_append.return_value = True

        run(append_audit_log_or_503(
            actor_uid="ACTOR_X", event_type="EVENT_X", target_id="target-1", status="STARTED",
        ))

        mock_append.assert_called_once_with(
            actor_uid="ACTOR_X", event_type="EVENT_X", target_id="target-1", status="STARTED",
        )


if __name__ == "__main__":
    unittest.main()