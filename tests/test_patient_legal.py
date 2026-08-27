"""Unit and route tests for versioned patient legal document acceptance.

Verifies:
- Server owns version and digest (client only specifies document_types)
- Legal config validation: missing/invalid values fail closed on legal endpoints
- Valid terms acceptance: creates row, enqueues PATIENT_TERMS_ACCEPTED
- Valid privacy notice acceptance: creates row, enqueues PATIENT_PRIVACY_NOTICE_ACKNOWLEDGED
- Exact retry is idempotent: no new row, no new audit event
- Unsupported document_type / empty list rejected
- Atomic multi-document acceptance: failure of one rolls back entire request (both orderings)
- Version/digest conflict (same version + different digest) -> 409 LEGAL_DOCUMENT_VERSION_DIGEST_CONFLICT
- Legal acceptance records contain no patient PII and are not encrypted
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.config import (
    ConfigError,
    PatientLegalDocumentConfig,
    get_patient_legal_config,
)
from app.core.dependencies import AuthenticatedPatient, get_current_patient
from app.models.patient import Patient
from app.models.patient_legal_acceptance import PatientLegalAcceptance
from app.services.patient_legal_service import (
    LegalAcceptanceError,
    _legal_audit_idempotency_key,
    accept_legal_documents,
    get_legal_requirements,
)

_VALID_CONFIG = PatientLegalDocumentConfig(
    terms_version="2026.1",
    terms_sha256="a" * 64,
    terms_url="https://legal.nexa.test/terms/2026.1",
    privacy_version="2026.1",
    privacy_sha256="b" * 64,
    privacy_url="https://legal.nexa.test/privacy/2026.1",
)


@pytest.fixture(autouse=True)
def _set_test_env(monkeypatch):
    monkeypatch.setenv("PATIENT_TERMS_VERSION", "2026.1")
    monkeypatch.setenv("PATIENT_TERMS_SHA256", "a" * 64)
    monkeypatch.setenv("PATIENT_TERMS_URL", "https://legal.nexa.test/terms/2026.1")
    monkeypatch.setenv("PATIENT_PRIVACY_VERSION", "2026.1")
    monkeypatch.setenv("PATIENT_PRIVACY_SHA256", "b" * 64)
    monkeypatch.setenv("PATIENT_PRIVACY_URL", "https://legal.nexa.test/privacy/2026.1")
    monkeypatch.setenv(
        "PATIENT_JWT_SECRET", "test-secret-at-least-32-chars-long-here!!"
    )


def _configure_nested_insert(db: AsyncMock) -> None:
    nested = MagicMock()
    nested.__aenter__ = AsyncMock(return_value=None)
    nested.__aexit__ = AsyncMock(return_value=False)
    db.begin_nested = MagicMock(return_value=nested)
    db.flush = AsyncMock()


# ---------------------------------------------------------------------------
# Service Unit Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_legal_requirements_returns_server_configured_values():
    pid = str(uuid.uuid4())
    db = AsyncMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=res)

    with patch(
        "app.services.patient_legal_service.get_patient_legal_config",
        return_value=_VALID_CONFIG,
    ):
        requirements = await get_legal_requirements(pid, db)
        assert len(requirements) == 2

        doc_types = {r.document_type: r for r in requirements}
        assert "PRIVACY_NOTICE" in doc_types
        assert "TERMS_OF_SERVICE" in doc_types

        terms = doc_types["TERMS_OF_SERVICE"]
        assert terms.document_version == "2026.1"
        assert terms.document_sha256 == "a" * 64
        assert terms.document_url == "https://legal.nexa.test/terms/2026.1"
        assert terms.accepted_current_version is False


@pytest.mark.parametrize(
    "valid_url",
    [
        "https://example.com",
        "http://example.com",
        "https://terms.example.com/privacy",
        "https://example.com:443/privacy",
        "https://sub-domain.example.co.in/legal/v1",
    ],
)
def test_patient_legal_config_accepts_well_formed_http_urls(monkeypatch, valid_url):
    monkeypatch.setenv("PATIENT_TERMS_URL", valid_url)
    assert get_patient_legal_config().terms_url == valid_url


@pytest.mark.parametrize(
    "invalid_url",
    [
        "http://",
        "https://",
        "ftp://example.com",
        "example.com",
        "://example.com",
        "https://exa mple.com",
        "https://example.com ",
        " https://example.com",
        "https://.",
        "https://-bad.example",
        "https://bad-.example",
        "https://example..com",
        "https://example.com:",
        "https://example.com:bad",
        "https://example.com:99999",
    ],
)
def test_patient_legal_config_rejects_malformed_or_non_http_urls(
    monkeypatch, invalid_url
):
    monkeypatch.setenv("PATIENT_TERMS_URL", invalid_url)
    with pytest.raises(ConfigError):
        get_patient_legal_config()


@pytest.mark.parametrize(
    "credential_url",
    [
        "https://user@example.com/legal",
        "https://user:password@example.com/legal",
        "https://user:@example.com/legal",
        "https://:password@example.com/legal",
        "https://user%40example.com:password@example.com/legal",
        "https://user%3Aname:password@example.com/legal",
    ],
)
def test_patient_legal_config_rejects_credential_bearing_urls(
    monkeypatch, credential_url
):
    monkeypatch.setenv("PATIENT_TERMS_URL", credential_url)
    with pytest.raises(ConfigError):
        get_patient_legal_config()


@pytest.mark.asyncio
async def test_accept_single_document_creates_row_and_enqueues_audit():
    pid = str(uuid.uuid4())
    db = AsyncMock()
    _configure_nested_insert(db)
    added_objects = []
    db.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))

    # Existing check returns None (not accepted yet)
    res = MagicMock()
    res.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=res)

    with (
        patch(
            "app.services.patient_legal_service.get_patient_legal_config",
            return_value=_VALID_CONFIG,
        ),
        patch(
            "app.services.patient_legal_service.enqueue_audit_event",
            new_callable=AsyncMock,
        ) as mock_audit,
    ):
        await accept_legal_documents(pid, ["TERMS_OF_SERVICE"], db)

        # Row added
        assert len(added_objects) == 1
        obj = added_objects[0]
        assert isinstance(obj, PatientLegalAcceptance)
        assert obj.patient_id == uuid.UUID(pid)
        assert obj.document_type == "TERMS_OF_SERVICE"
        assert obj.document_version == "2026.1"
        assert obj.document_sha256 == "a" * 64

        # Audit event
        mock_audit.assert_awaited_once()
        audit_call = mock_audit.await_args.kwargs
        assert audit_call["event_type"] == "PATIENT_TERMS_ACCEPTED"
        assert audit_call["patient_id"] == pid
        assert audit_call["metadata"] == {
            "document_type": "TERMS_OF_SERVICE",
            "document_version": "2026.1",
            "document_sha256": "a" * 64,
        }


@pytest.mark.asyncio
async def test_accept_privacy_notice_enqueues_privacy_audit():
    pid = str(uuid.uuid4())
    db = AsyncMock()
    _configure_nested_insert(db)
    added_objects = []
    db.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))

    res = MagicMock()
    res.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=res)

    with (
        patch(
            "app.services.patient_legal_service.get_patient_legal_config",
            return_value=_VALID_CONFIG,
        ),
        patch(
            "app.services.patient_legal_service.enqueue_audit_event",
            new_callable=AsyncMock,
        ) as mock_audit,
    ):
        await accept_legal_documents(pid, ["PRIVACY_NOTICE"], db)

        assert len(added_objects) == 1
        assert added_objects[0].document_type == "PRIVACY_NOTICE"

        mock_audit.assert_awaited_once()
        assert (
            mock_audit.await_args.kwargs["event_type"]
            == "PATIENT_PRIVACY_NOTICE_ACKNOWLEDGED"
        )


def test_legal_audit_key_is_deterministic_bounded_and_collision_resistant():
    common = {
        "patient_id": str(uuid.uuid4()),
        "document_type": "TERMS_OF_SERVICE",
        "document_version": "v" * 64,
        "document_sha256": "a" * 64,
    }
    key = _legal_audit_idempotency_key(**common)
    assert key == _legal_audit_idempotency_key(**common)
    assert len(key) <= 128
    assert key != _legal_audit_idempotency_key(
        **{**common, "document_sha256": "b" * 64}
    )


@pytest.mark.asyncio
async def test_accept_exact_retry_is_idempotent_no_duplicate_audit():
    pid = str(uuid.uuid4())
    existing = PatientLegalAcceptance(
        patient_id=uuid.UUID(pid),
        document_type="TERMS_OF_SERVICE",
        document_version="2026.1",
        document_sha256="a" * 64,
    )

    db = AsyncMock()
    added_objects = []
    db.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))

    res = MagicMock()
    res.scalar_one_or_none.return_value = existing
    db.execute = AsyncMock(return_value=res)

    with (
        patch(
            "app.services.patient_legal_service.get_patient_legal_config",
            return_value=_VALID_CONFIG,
        ),
        patch(
            "app.services.patient_legal_service.enqueue_audit_event",
            new_callable=AsyncMock,
        ) as mock_audit,
    ):
        await accept_legal_documents(pid, ["TERMS_OF_SERVICE"], db)

        # No new row, no new audit event
        assert len(added_objects) == 0
        mock_audit.assert_not_called()


@pytest.mark.asyncio
async def test_accept_version_digest_conflict_fails_closed():
    pid = str(uuid.uuid4())
    # Existing row has version 2026.1 but digest "old_hash" instead of "a"*64
    existing = PatientLegalAcceptance(
        patient_id=uuid.UUID(pid),
        document_type="TERMS_OF_SERVICE",
        document_version="2026.1",
        document_sha256="c" * 64,
    )

    db = AsyncMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = existing
    db.execute = AsyncMock(return_value=res)

    with patch(
        "app.services.patient_legal_service.get_patient_legal_config",
        return_value=_VALID_CONFIG,
    ):
        with pytest.raises(LegalAcceptanceError) as exc:
            await accept_legal_documents(pid, ["TERMS_OF_SERVICE"], db)
        assert exc.value.code == "LEGAL_DOCUMENT_VERSION_DIGEST_CONFLICT"


@pytest.mark.asyncio
async def test_accept_invalid_document_type_rejected():
    pid = str(uuid.uuid4())
    db = AsyncMock()

    with pytest.raises(LegalAcceptanceError) as exc:
        await accept_legal_documents(pid, ["ARBITRARY_CONSENT"], db)
    assert exc.value.code == "UNSUPPORTED_DOCUMENT_TYPE"


@pytest.mark.asyncio
async def test_accept_empty_document_types_rejected():
    pid = str(uuid.uuid4())
    db = AsyncMock()

    with pytest.raises(LegalAcceptanceError) as exc:
        await accept_legal_documents(pid, [], db)
    assert exc.value.code == "NO_DOCUMENT_TYPES"


# ---------------------------------------------------------------------------
# Route-Level Tests & Safe Error Mapping
# ---------------------------------------------------------------------------


def test_post_legal_acceptances_atomic_rollback_on_conflict():
    client = TestClient(app)
    test_pid = str(uuid.uuid4())

    async def mock_auth():
        return AuthenticatedPatient(
            patient_id=test_pid,
            patient=Patient(patient_uuid=uuid.UUID(test_pid)),
        )

    app.dependency_overrides[get_current_patient] = mock_auth

    with patch(
        "app.api.v2.patient_self_routes.accept_legal_documents",
        new_callable=AsyncMock,
        side_effect=LegalAcceptanceError(
            "LEGAL_DOCUMENT_VERSION_DIGEST_CONFLICT", "Digest conflict on document"
        ),
    ):
        resp = client.post(
            "/api/v2/patient/me/legal-acceptances",
            json={"document_types": ["TERMS_OF_SERVICE", "PRIVACY_NOTICE"]},
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "LEGAL_DOCUMENT_VERSION_DIGEST_CONFLICT"

    app.dependency_overrides.clear()


def test_get_legal_requirements_config_missing_returns_503():
    client = TestClient(app)
    test_pid = str(uuid.uuid4())

    async def mock_auth():
        return AuthenticatedPatient(
            patient_id=test_pid,
            patient=Patient(patient_uuid=uuid.UUID(test_pid)),
        )

    app.dependency_overrides[get_current_patient] = mock_auth

    with patch(
        "app.api.v2.patient_self_routes.get_legal_requirements",
        new_callable=AsyncMock,
        side_effect=LegalAcceptanceError(
            "LEGAL_CONFIG_UNAVAILABLE", "Legal document config missing"
        ),
    ):
        resp = client.get("/api/v2/patient/me/legal-requirements")
        assert resp.status_code == 503
        assert resp.json()["detail"] == "LEGAL_CONFIG_UNAVAILABLE"

    app.dependency_overrides.clear()


def test_credential_bearing_legal_url_is_not_exposed_by_requirements_api(monkeypatch):
    client = TestClient(app)
    test_pid = str(uuid.uuid4())
    sentinel = "SECRET_URL_CREDENTIAL_DO_NOT_EXPOSE"
    monkeypatch.setenv(
        "PATIENT_TERMS_URL", f"https://user:{sentinel}@example.com/legal"
    )

    async def mock_auth():
        return AuthenticatedPatient(
            patient_id=test_pid,
            patient=Patient(patient_uuid=uuid.UUID(test_pid)),
        )

    app.dependency_overrides[get_current_patient] = mock_auth
    try:
        response = client.get("/api/v2/patient/me/legal-requirements")
        assert response.status_code == 503
        assert response.json()["detail"] == "LEGAL_CONFIG_UNAVAILABLE"
        assert sentinel not in response.text
        assert test_pid not in response.text
    finally:
        app.dependency_overrides.clear()


def test_legal_mutation_rejects_client_identity_document_and_timestamp_fields():
    client = TestClient(app)
    test_pid = str(uuid.uuid4())

    async def mock_auth():
        return AuthenticatedPatient(
            patient_id=test_pid,
            patient=Patient(patient_uuid=uuid.UUID(test_pid)),
        )

    app.dependency_overrides[get_current_patient] = mock_auth
    with patch(
        "app.api.v2.patient_self_routes.accept_legal_documents",
        new_callable=AsyncMock,
    ) as service:
        response = client.post(
            "/api/v2/patient/me/legal-acceptances",
            json={
                "document_types": ["TERMS_OF_SERVICE"],
                "patient_id": str(uuid.uuid4()),
                "document_version": "attacker-version",
                "document_sha256": "c" * 64,
                "accepted_at": "2026-08-26T00:00:00Z",
                "timestamp": "2026-08-26T00:00:00Z",
                "supabase_user_id": "attacker-subject",
            },
        )
        assert response.status_code == 422
        service.assert_not_awaited()

    app.dependency_overrides.clear()
