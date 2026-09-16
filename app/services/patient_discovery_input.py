"""Strict normalization for non-database patient discovery inputs."""

from __future__ import annotations

import re

from app.services.patient_discovery_service import normalize_public_patient_id

_QR_PUBLIC_ID_RE = re.compile(
    r"^nexa://patient-discovery/v1/(NC-[A-Fa-f0-9]{24})$"
)


def normalize_qr_public_id(value: str) -> str:
    """Extract only the versioned opaque public patient ID from a Nexa QR.

    Query strings, fragments, percent-encoded variants, raw UUIDs, access
    tokens, consent tokens, and arbitrary URLs are intentionally rejected.
    """

    candidate = value.strip()
    match = _QR_PUBLIC_ID_RE.fullmatch(candidate)
    if match is None:
        raise ValueError("INVALID_DISCOVERY_QR")
    return normalize_public_patient_id(match.group(1))
