from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.security.clinical_access_policy import ClinicalAccessOperation
from app.services.clinical_access_session import (
    ClinicalAccessSessionError,
    binding_matches,
    build_from_signed_v3_approval,
    hash_provider_session_binding,
)


NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


def approved_request(**overrides) -> dict:
    request = {
        "protocol_version": "nexa-consent-v3",
        "request_id": "request-1",
        "patient_id": "00000000-0000-0000-0000-000000000001",
        "provider_id": "00000000-0000-0000-0000-000000000002",
        "hospital_id": "00000000-0000-0000-0000-000000000003",
        "purpose": "routine_checkup",
        "scope": "clinical",
        "status": "approved",
        "access_expires_at": (NOW + timedelta(minutes=15)).isoformat(),
    }
    request.update(overrides)
    return request


def test_v3_session_constructor_preserves_read_authority_without_write_escalation() -> None:
    session = build_from_signed_v3_approval(
        request_data=approved_request(),
        provider_session_binding="provider-session-a",
        now=NOW,
    )

    assert session.patient_id == "00000000-0000-0000-0000-000000000001"
    assert session.provider_id == "00000000-0000-0000-0000-000000000002"
    assert session.hospital_id == "00000000-0000-0000-0000-000000000003"
    assert session.consent_request_id == "request-1"
    assert session.allowed_operations == (
        ClinicalAccessOperation.READ_CLINICAL_HISTORY.value,
    )
    assert session.allows(ClinicalAccessOperation.READ_CLINICAL_HISTORY, at=NOW)
    assert not session.allows(ClinicalAccessOperation.CREATE_ENCOUNTER, at=NOW)
    assert not session.allows(ClinicalAccessOperation.WRITE_PRESCRIPTION, at=NOW)
    assert not session.allows(ClinicalAccessOperation.WRITE_DIAGNOSIS, at=NOW)
    assert not session.allows(ClinicalAccessOperation.WRITE_VITALS, at=NOW)
    assert not session.allows(ClinicalAccessOperation.WRITE_CLINICAL_NOTES, at=NOW)


def test_provider_session_binding_is_hashed_and_exactly_bound() -> None:
    raw = "provider-session-a"
    session = build_from_signed_v3_approval(
        request_data=approved_request(),
        provider_session_binding=raw,
        now=NOW,
    )

    assert session.provider_session_binding_hash == hash_provider_session_binding(raw)
    assert session.provider_session_binding_hash != raw
    assert binding_matches(session, raw)
    assert not binding_matches(session, "provider-session-b")


def test_session_id_is_server_generated_per_session() -> None:
    first = build_from_signed_v3_approval(
        request_data=approved_request(),
        provider_session_binding="provider-session-a",
        now=NOW,
    )
    second = build_from_signed_v3_approval(
        request_data=approved_request(),
        provider_session_binding="provider-session-a",
        now=NOW,
    )
    assert first.session_id != second.session_id


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"protocol_version": "nexa-consent-v2"}, "CLINICAL_SESSION_PROTOCOL_MISMATCH"),
        ({"status": "pending"}, "CLINICAL_SESSION_CONSENT_NOT_APPROVED"),
        ({"status": "denied"}, "CLINICAL_SESSION_CONSENT_NOT_APPROVED"),
        ({"purpose": "document_processing", "scope": "documents"}, "CLINICAL_SESSION_SCOPE_NOT_ELIGIBLE"),
        ({"scope": "documents"}, "CLINICAL_SESSION_SCOPE_NOT_ELIGIBLE"),
        ({"scope": "unknown"}, "CLINICAL_SESSION_SCOPE_NOT_ELIGIBLE"),
        ({"access_expires_at": "2026-09-16T11:59:59+00:00"}, "CLINICAL_SESSION_ACCESS_EXPIRED"),
        ({"access_expires_at": "2026-09-16T12:15:00"}, "CLINICAL_SESSION_EXPIRY_INVALID"),
        ({"patient_id": ""}, "CLINICAL_SESSION_PATIENT_ID_REQUIRED"),
        ({"provider_id": ""}, "CLINICAL_SESSION_PROVIDER_ID_REQUIRED"),
        ({"hospital_id": ""}, "CLINICAL_SESSION_HOSPITAL_ID_REQUIRED"),
        ({"request_id": ""}, "CLINICAL_SESSION_REQUEST_ID_REQUIRED"),
    ],
)
def test_session_construction_fails_closed(overrides: dict, code: str) -> None:
    with pytest.raises(ClinicalAccessSessionError) as exc:
        build_from_signed_v3_approval(
            request_data=approved_request(**overrides),
            provider_session_binding="provider-session-a",
            now=NOW,
        )
    assert exc.value.code == code


def test_missing_provider_session_binding_is_rejected() -> None:
    with pytest.raises(ClinicalAccessSessionError) as exc:
        build_from_signed_v3_approval(
            request_data=approved_request(),
            provider_session_binding=" ",
            now=NOW,
        )
    assert exc.value.code == "CLINICAL_SESSION_BINDING_REQUIRED"


def test_expired_session_never_authorizes_operation() -> None:
    session = build_from_signed_v3_approval(
        request_data=approved_request(),
        provider_session_binding="provider-session-a",
        now=NOW,
    )
    after_expiry = NOW + timedelta(minutes=16)
    assert not session.is_active(at=after_expiry)
    assert not session.allows(
        ClinicalAccessOperation.READ_CLINICAL_HISTORY,
        at=after_expiry,
    )
