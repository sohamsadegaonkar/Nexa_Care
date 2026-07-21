from typing import Any

SENSITIVE_FIELDS = {
    "patient_name",
    "phone",
    "aadhaar",
    "aadhaar_abha_id",
    "email",
    "dob",
    "nfc_uid",
    "bio_seed",
    "derived_alpha",
}


def redact_payload(data: Any) -> Any:
    if isinstance(data, dict):
        return {
            k: ("[REDACTED]" if k.lower() in SENSITIVE_FIELDS else redact_payload(v))
            for k, v in data.items()
        }
    elif isinstance(data, list):
        return [redact_payload(i) for i in data]
    return data
