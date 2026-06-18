import re
from typing import Any, Dict, List, Union

# Canonical PII field-name set for Nexa Care. This is the single source of
# truth -- both log redaction (redact_payload, below) and the OCR-output
# vault/clinical sharding decision (app/services/sharding.py) import this
# same set, so the two can no longer drift apart the way they previously
# did (this used to say "aadhaar" here while models/schemas.py and the
# /register payload both used "aadhaar_abha_id", so a field extracted under
# the schema's actual name silently fell through into the "anonymized"
# clinical shard instead of the PII vault).
SENSITIVE_FIELDS = {
    "patient_name", "phone", "aadhaar_abha_id", "email", "dob"
}

def redact_payload(data: Any) -> Any:
    if isinstance(data, dict):
        return {k: ("[REDACTED]" if k.lower() in SENSITIVE_FIELDS else redact_payload(v)) 
                for k, v in data.items()}
    elif isinstance(data, list):
        return [redact_payload(i) for i in data]
    return data