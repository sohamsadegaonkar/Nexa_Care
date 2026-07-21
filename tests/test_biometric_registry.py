import asyncio
import unittest
from unittest.mock import patch, MagicMock

from fastapi import HTTPException

from app.services.biometric_registry import (
    compute_bio_verifier,
    verify_biometric_binding,
    enroll_biometric_binding,
    enroll_biometric_binding_with_audit,
)
from app.core.config import HandshakeConfig


def run(coro):
    return asyncio.run(coro)


class FakeResult:
    def __init__(self, error=None, data=None):
        self.error = error
        self.data = data


def make_fake_select_client(result):
    mock_table = MagicMock()
    mock_table.select.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.single.return_value = mock_table
    mock_table.execute.return_value = result

    mock_client = MagicMock()
    mock_client.table.return_value = mock_table
    return mock_client, mock_table


def make_fake_insert_client(result):
    mock_table = MagicMock()
    mock_table.insert.return_value = mock_table
    mock_table.execute.return_value = result

    mock_client = MagicMock()
    mock_client.table.return_value = mock_table
    return mock_client, mock_table


class TestComputeBioVerifier(unittest.TestCase):
    @patch("app.services.biometric_registry.get_handshake_config")
    def test_deterministic_for_same_inputs(self, mock_config):
        mock_config.return_value = HandshakeConfig(pepper_secret="test-pepper")
        h1 = compute_bio_verifier("NFC-001", "pulse-data")
        h2 = compute_bio_verifier("NFC-001", "pulse-data")
        self.assertEqual(h1, h2)

    @patch("app.services.biometric_registry.get_handshake_config")
    def test_different_bio_seed_changes_verifier(self, mock_config):
        mock_config.return_value = HandshakeConfig(pepper_secret="test-pepper")
        h1 = compute_bio_verifier("NFC-001", "pulse-data-A")
        h2 = compute_bio_verifier("NFC-001", "pulse-data-B")
        self.assertNotEqual(h1, h2)

    @patch("app.services.biometric_registry.get_handshake_config")
    def test_different_pepper_changes_verifier(self, mock_config):
        mock_config.return_value = HandshakeConfig(pepper_secret="pepper-A")
        h1 = compute_bio_verifier("NFC-001", "pulse-data")
        mock_config.return_value = HandshakeConfig(pepper_secret="pepper-B")
        h2 = compute_bio_verifier("NFC-001", "pulse-data")
        self.assertNotEqual(h1, h2)

    @patch("app.services.biometric_registry.get_handshake_config")
    def test_output_is_sha256_hexdigest(self, mock_config):
        mock_config.return_value = HandshakeConfig(pepper_secret="test-pepper")
        h = compute_bio_verifier("NFC-001", "pulse-data")
        self.assertEqual(len(h), 64)
        int(h, 16)


class TestVerifyBiometricBinding(unittest.TestCase):
    @patch("app.services.biometric_registry.compute_bio_verifier")
    @patch("app.services.biometric_registry.get_supabase_client")
    def test_matching_unrevoked_binding_returns_true(
        self, mock_get_client, mock_compute
    ):
        mock_compute.return_value = "abc123hash"
        client, _ = make_fake_select_client(
            FakeResult(
                error=None, data={"bio_verifier_hash": "abc123hash", "revoked_at": None}
            )
        )
        mock_get_client.return_value = client

        result = run(
            verify_biometric_binding("NFC-001", "pulse-data", "patient-uuid-123")
        )
        self.assertTrue(result)

    @patch("app.services.biometric_registry.compute_bio_verifier")
    @patch("app.services.biometric_registry.get_supabase_client")
    def test_mismatched_verifier_returns_false(self, mock_get_client, mock_compute):
        mock_compute.return_value = "wrong-hash"
        client, _ = make_fake_select_client(
            FakeResult(
                error=None, data={"bio_verifier_hash": "abc123hash", "revoked_at": None}
            )
        )
        mock_get_client.return_value = client

        result = run(
            verify_biometric_binding("NFC-001", "pulse-data", "patient-uuid-123")
        )
        self.assertFalse(result)

    @patch("app.services.biometric_registry.compute_bio_verifier")
    @patch("app.services.biometric_registry.get_supabase_client")
    def test_revoked_binding_returns_false_even_if_verifier_matches(
        self, mock_get_client, mock_compute
    ):
        mock_compute.return_value = "abc123hash"
        client, _ = make_fake_select_client(
            FakeResult(
                error=None,
                data={
                    "bio_verifier_hash": "abc123hash",
                    "revoked_at": "2026-01-01T00:00:00Z",
                },
            )
        )
        mock_get_client.return_value = client

        result = run(
            verify_biometric_binding("NFC-001", "pulse-data", "patient-uuid-123")
        )
        self.assertFalse(result)

    @patch("app.services.biometric_registry.get_supabase_client")
    def test_no_binding_row_returns_false(self, mock_get_client):
        client, _ = make_fake_select_client(FakeResult(error=None, data=None))
        mock_get_client.return_value = client

        result = run(
            verify_biometric_binding("NFC-001", "pulse-data", "patient-uuid-123")
        )
        self.assertFalse(result)

    @patch("app.services.biometric_registry.get_supabase_client")
    def test_db_error_returns_false(self, mock_get_client):
        client, _ = make_fake_select_client(
            FakeResult(error="connection reset", data=None)
        )
        mock_get_client.return_value = client

        result = run(
            verify_biometric_binding("NFC-001", "pulse-data", "patient-uuid-123")
        )
        self.assertFalse(result)

    @patch("app.services.biometric_registry.get_supabase_client")
    def test_unexpected_exception_returns_false(self, mock_get_client):
        mock_get_client.side_effect = ConnectionError("supabase unreachable")

        result = run(
            verify_biometric_binding("NFC-001", "pulse-data", "patient-uuid-123")
        )
        self.assertFalse(result)


class TestEnrollBiometricBinding(unittest.TestCase):
    # Note: `db` is required positionally (Sprint 2 added it to support
    # encrypting an optional device_public_key via the KMS). These tests
    # never pass device_public_key, so `db` is never actually touched --
    # a bare MagicMock() is enough to satisfy the signature.
    @patch("app.services.biometric_registry.compute_bio_verifier")
    @patch("app.services.biometric_registry.get_supabase_client")
    def test_success_returns_true(self, mock_get_client, mock_compute):
        mock_compute.return_value = "abc123hash"
        client, table = make_fake_insert_client(
            FakeResult(error=None, data=[{"id": "row-1"}])
        )
        mock_get_client.return_value = client

        result = run(
            enroll_biometric_binding(
                "NFC-001", "pulse-data", "patient-uuid-123", MagicMock()
            )
        )

        self.assertTrue(result)
        inserted_row = table.insert.call_args[0][0]
        self.assertEqual(inserted_row["masked_internal_id"], "patient-uuid-123")
        self.assertEqual(inserted_row["bio_verifier_hash"], "abc123hash")

    @patch("app.services.biometric_registry.compute_bio_verifier")
    @patch("app.services.biometric_registry.get_supabase_client")
    def test_db_error_returns_false(self, mock_get_client, mock_compute):
        mock_compute.return_value = "abc123hash"
        client, _ = make_fake_insert_client(
            FakeResult(error="unique violation", data=None)
        )
        mock_get_client.return_value = client

        result = run(
            enroll_biometric_binding(
                "NFC-001", "pulse-data", "patient-uuid-123", MagicMock()
            )
        )
        self.assertFalse(result)


class TestEnrollBiometricBindingWithAudit(unittest.TestCase):
    @patch("app.services.biometric_registry.enroll_biometric_binding")
    @patch("app.services.biometric_registry.append_audit_log_or_503")
    def test_success_logs_attempt_then_success_in_order(self, mock_audit, mock_enroll):
        mock_enroll.return_value = True

        result = run(
            enroll_biometric_binding_with_audit(
                nfc_uid="NFC-001",
                bio_seed="pulse-data",
                masked_internal_id="patient-uuid-123",
                db=MagicMock(),
            )
        )

        self.assertTrue(result)
        self.assertEqual(mock_audit.call_count, 2)
        first_call, second_call = mock_audit.call_args_list
        self.assertEqual(
            first_call.kwargs["event_type"], "BIOMETRIC_ENROLLMENT_ATTEMPT"
        )
        self.assertEqual(
            second_call.kwargs["event_type"], "BIOMETRIC_ENROLLMENT_SUCCESS"
        )

    @patch("app.services.biometric_registry.enroll_biometric_binding")
    @patch("app.services.biometric_registry.append_audit_log_or_503")
    def test_db_write_failure_logs_failed_event_then_raises_502(
        self, mock_audit, mock_enroll
    ):
        mock_enroll.return_value = False

        with self.assertRaises(HTTPException) as cm:
            run(
                enroll_biometric_binding_with_audit(
                    nfc_uid="NFC-001",
                    bio_seed="pulse-data",
                    masked_internal_id="patient-uuid-123",
                    db=MagicMock(),
                )
            )

        self.assertEqual(cm.exception.status_code, 502)
        self.assertEqual(mock_audit.call_count, 2)
        first_call, second_call = mock_audit.call_args_list
        self.assertEqual(
            first_call.kwargs["event_type"], "BIOMETRIC_ENROLLMENT_ATTEMPT"
        )
        self.assertEqual(
            second_call.kwargs["event_type"], "BIOMETRIC_ENROLLMENT_FAILED"
        )

    @patch("app.services.biometric_registry.enroll_biometric_binding")
    @patch("app.services.biometric_registry.append_audit_log_or_503")
    def test_audit_write_failure_on_attempt_aborts_before_any_db_write(
        self, mock_audit, mock_enroll
    ):
        # The attempt log itself fails to write -- must hard-fail with 503
        # immediately, and the actual registry insert must never be
        # attempted at all.
        mock_audit.side_effect = HTTPException(status_code=503, detail="audit down")

        with self.assertRaises(HTTPException) as cm:
            run(
                enroll_biometric_binding_with_audit(
                    nfc_uid="NFC-001",
                    bio_seed="pulse-data",
                    masked_internal_id="patient-uuid-123",
                    db=MagicMock(),
                )
            )

        self.assertEqual(cm.exception.status_code, 503)
        mock_enroll.assert_not_called()

    @patch("app.services.biometric_registry.enroll_biometric_binding")
    @patch("app.services.biometric_registry.append_audit_log_or_503")
    def test_audit_write_failure_on_success_log_still_raises_503(
        self, mock_audit, mock_enroll
    ):
        # Enrollment itself succeeded, but we can't audit-log that fact --
        # must still hard-fail (503), never silently return a "success"
        # the system can't prove it logged.
        mock_enroll.return_value = True
        mock_audit.side_effect = [
            None,
            HTTPException(status_code=503, detail="audit down"),
        ]

        with self.assertRaises(HTTPException) as cm:
            run(
                enroll_biometric_binding_with_audit(
                    nfc_uid="NFC-001",
                    bio_seed="pulse-data",
                    masked_internal_id="patient-uuid-123",
                    db=MagicMock(),
                )
            )

        self.assertEqual(cm.exception.status_code, 503)

    @patch("app.services.biometric_registry.enroll_biometric_binding")
    @patch("app.services.biometric_registry.append_audit_log_or_503")
    def test_passes_through_correct_arguments_to_enroll(self, mock_audit, mock_enroll):
        mock_enroll.return_value = True
        fake_db = MagicMock()

        run(
            enroll_biometric_binding_with_audit(
                nfc_uid="NFC-XYZ",
                bio_seed="seed-xyz",
                masked_internal_id="patient-abc",
                db=fake_db,
            )
        )

        # enroll_biometric_binding_with_audit forwards db and device_public_key
        # through to enroll_biometric_binding unchanged (biometric_registry.py:358-364).
        mock_enroll.assert_called_once_with(
            nfc_uid="NFC-XYZ",
            bio_seed="seed-xyz",
            masked_internal_id="patient-abc",
            db=fake_db,
            device_public_key=None,
        )


if __name__ == "__main__":
    unittest.main()
