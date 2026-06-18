import unittest

from app.observability.redactor import redact_payload, SENSITIVE_FIELDS


class TestRedactPayload(unittest.TestCase):

    def test_top_level_sensitive_field_is_redacted(self):
        result = redact_payload({"patient_name": "Asha Rao", "status": "ok"})
        self.assertEqual(result, {"patient_name": "[REDACTED]", "status": "ok"})

    def test_aadhaar_abha_id_is_redacted_regression(self):
        # This is the actual schema field name (schemas.py); the old
        # SENSITIVE_FIELDS set only had "aadhaar", which never matched it.
        result = redact_payload({"aadhaar_abha_id": "1234-5678-9012"})
        self.assertEqual(result, {"aadhaar_abha_id": "[REDACTED]"})

    def test_biometric_fields_are_redacted(self):
        result = redact_payload({
            "nfc_uid": "NFC-001",
            "bio_seed": "raw-pulse-data",
            "derived_alpha": "9f8e7d6c",
        })
        self.assertEqual(result, {
            "nfc_uid": "[REDACTED]",
            "bio_seed": "[REDACTED]",
            "derived_alpha": "[REDACTED]",
        })

    def test_case_insensitive_match(self):
        result = redact_payload({"Patient_Name": "Asha Rao", "PHONE": "9999999999"})
        self.assertEqual(result, {"Patient_Name": "[REDACTED]", "PHONE": "[REDACTED]"})

    def test_non_sensitive_fields_are_untouched(self):
        result = redact_payload({"diagnoses": ["hypertension"], "visit_count": 3})
        self.assertEqual(result, {"diagnoses": ["hypertension"], "visit_count": 3})

    def test_nested_dict_is_recursively_redacted(self):
        # Mirrors the actual shape logged in main.py: vault_payload nested
        # under a wrapper key.
        result = redact_payload({
            "raw_pii": {"patient_name": "Asha Rao", "phone": "9999999999"},
            "masked_internal_id": "abc-123",
        })
        self.assertEqual(result, {
            "raw_pii": {"patient_name": "[REDACTED]", "phone": "[REDACTED]"},
            "masked_internal_id": "abc-123",
        })

    def test_list_of_dicts_is_redacted(self):
        result = redact_payload([
            {"patient_name": "Asha Rao"},
            {"patient_name": "Vikram Singh"},
        ])
        self.assertEqual(result, [
            {"patient_name": "[REDACTED]"},
            {"patient_name": "[REDACTED]"},
        ])

    def test_scalar_passthrough(self):
        self.assertEqual(redact_payload("just a string"), "just a string")
        self.assertEqual(redact_payload(42), 42)
        self.assertIsNone(redact_payload(None))

    def test_email_and_dob_still_covered(self):
        # Guard against a future edit accidentally dropping existing fields
        # while fixing the aadhaar bug.
        self.assertIn("email", SENSITIVE_FIELDS)
        self.assertIn("dob", SENSITIVE_FIELDS)


if __name__ == "__main__":
    unittest.main()