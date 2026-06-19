import asyncio
import unittest
from unittest.mock import patch, MagicMock

from fastapi import HTTPException

from app.observability.audit_ledger import _calculate_hash, append_audit_log, append_audit_log_or_503
from app.core.request_context import trace_id_var


def run(coro):
    return asyncio.run(coro)


class FakeResult:
    def __init__(self, error=None, data=None):
        self.error = error
        self.data = data


def make_fake_supabase(select_result, insert_result=None):
    """Builds a fake supabase client matching the fluent
    .table(...).select(...).order(...).limit(...).execute() /
    .table(...).insert(...).execute() call chains used by audit_ledger.
    """
    mock_table = MagicMock()
    mock_table.select.return_value = mock_table
    mock_table.order.return_value = mock_table
    mock_table.limit.return_value = mock_table
    mock_table.insert.return_value = mock_table

    if insert_result is not None:
        mock_table.execute.side_effect = [select_result, insert_result]
    else:
        mock_table.execute.side_effect = [select_result]

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
        select_result = FakeResult(error=None, data=[{"record_hash": "prev-hash-abc"}])
        insert_result = FakeResult(error=None, data=[{"ok": True}])
        mock_client, mock_table = make_fake_supabase(select_result, insert_result)
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
        select_result = FakeResult(error=None, data=[])
        insert_result = FakeResult(error=None, data=[{"ok": True}])
        mock_client, mock_table = make_fake_supabase(select_result, insert_result)
        mock_get_client.return_value = mock_client

        result = run(append_audit_log("ACTOR", "EVENT", "target-1", "SUCCESS"))

        self.assertTrue(result)
        inserted_row = mock_table.insert.call_args[0][0]
        self.assertEqual(inserted_row["previous_hash"], "GENESIS")

    @patch("app.observability.audit_ledger.get_supabase_client")
    def test_read_error_returns_false_and_logs_critical(self, mock_get_client):
        select_result = FakeResult(error="connection reset", data=None)
        mock_client, mock_table = make_fake_supabase(select_result)
        mock_get_client.return_value = mock_client

        with self.assertLogs("nexa_logger", level="CRITICAL") as cm:
            result = run(append_audit_log("ACTOR", "EVENT", "target-1", "SUCCESS"))

        self.assertFalse(result)
        self.assertIn("audit_log_write_failed", cm.output[0])
        # Read failed, so we must never attempt the insert.
        mock_table.insert.assert_not_called()

    @patch("app.observability.audit_ledger.get_supabase_client")
    def test_insert_error_returns_false_and_logs_critical(self, mock_get_client):
        select_result = FakeResult(error=None, data=[])
        insert_result = FakeResult(error="constraint violation", data=None)
        mock_client, mock_table = make_fake_supabase(select_result, insert_result)
        mock_get_client.return_value = mock_client

        with self.assertLogs("nexa_logger", level="CRITICAL") as cm:
            result = run(append_audit_log("ACTOR", "EVENT", "target-1", "SUCCESS"))

        self.assertFalse(result)
        self.assertIn("audit_log_write_failed", cm.output[0])

    @patch("app.observability.audit_ledger.get_supabase_client")
    def test_unexpected_exception_returns_false_and_logs_critical(self, mock_get_client):
        mock_get_client.side_effect = ConnectionError("supabase unreachable")

        with self.assertLogs("nexa_logger", level="CRITICAL") as cm:
            result = run(append_audit_log("ACTOR", "EVENT", "target-1", "SUCCESS"))

        self.assertFalse(result)
        self.assertIn("audit_log_write_failed", cm.output[0])


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