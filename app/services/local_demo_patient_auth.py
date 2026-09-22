"""Strictly scoped synthetic patient identities for the local development demo.

These values are deliberately not a general authentication provider.  They
exist only so the two seeded synthetic patients can exercise the real patient
session and device-enrollment paths on a disposable development stack.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from app.core.config import ConfigError, RuntimeEnvironment, get_runtime_environment


LOCAL_DEMO_PATIENT_AUTH_PROVIDER = "local_demo"
LOCAL_DEMO_PATIENT_AUTH_METHOD = "local_demo"
LOCAL_DEMO_PATIENT_LOGIN_FLAG = "NEXA_DEMO_PATIENT_LOGIN_ENABLED"
LOCAL_DEMO_PATIENT_LOGIN_ALLOWED_HOSTS_FLAG = "NEXA_DEMO_PATIENT_LOGIN_ALLOWED_HOSTS"
LOCAL_DEMO_PATIENT_LOGIN_ALLOWED_CLIENT_CIDRS_FLAG = (
    "NEXA_DEMO_PATIENT_LOGIN_ALLOWED_CLIENT_CIDRS"
)

# Closed identifiers only.  The API never accepts a patient UUID, phone number,
# email address, or arbitrary upstream subject for this development-only path.
LOCAL_DEMO_PATIENT_SUBJECTS: Mapping[str, str] = {
    "aarav": "nexa-local-demo:patient:aarav",
    "priya": "nexa-local-demo:patient:priya",
}


def local_demo_patient_subject(demo_patient: str) -> str | None:
    """Resolve one of the closed synthetic-patient keys to its stable subject."""

    return LOCAL_DEMO_PATIENT_SUBJECTS.get(demo_patient)


def is_local_demo_patient_auth_enabled() -> bool:
    """Allow synthetic patient authentication only with both explicit local gates.

    Configuration errors, omitted flags, and every non-development environment
    are denials.  This function is also used while resolving an already-issued
    JWT, so turning the flag off immediately removes this authentication path.
    """

    try:
        environment = get_runtime_environment()
    except ConfigError:
        return False
    return (
        environment is RuntimeEnvironment.DEVELOPMENT
        and os.getenv(LOCAL_DEMO_PATIENT_LOGIN_FLAG, "").strip().lower() == "true"
    )


def is_supported_patient_auth_identity(*, provider: str, auth_method: str) -> bool:
    """Return whether the exact provider/method pair is currently permitted."""

    if provider == "supabase" and auth_method == "phone_otp":
        return True
    return (
        provider == LOCAL_DEMO_PATIENT_AUTH_PROVIDER
        and auth_method == LOCAL_DEMO_PATIENT_AUTH_METHOD
        and is_local_demo_patient_auth_enabled()
    )
