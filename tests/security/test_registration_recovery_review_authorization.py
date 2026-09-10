"""Security contracts for independent registration-recovery reviewer authority."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.core.registration_recovery_review_gate as gate
from app.models.patient_registration_recovery_review import (
    REGISTRATION_RECOVERY_REVIEW_AUTHORITY_VERSION,
    REGISTRATION_RECOVERY_REVIEWER_ROLE,
)
from app.models.provider import AffiliationTrustStatus, AffiliationType
from app.models.provider_context import (
    AffiliationContext,
    HospitalContext,
    ProviderContext,
    ProviderIdentityContext,
)


def _provider(*, token: str = "review-session") -> ProviderContext:
    provider_id = uuid.uuid4()
    hospital_id = uuid.uuid4()
    affiliation_id = uuid.uuid4()
    return ProviderContext(
        provider=ProviderIdentityContext(
            provider_id=provider_id,
            display_name="Reviewer",
            medical_registration_number=None,
            specialty=None,
            contact_email="reviewer@example.test",
        ),
        hospital=HospitalContext(
            hospital_id=hospital_id,
            facility_code="TEST",
            display_name="Test Hospital",
        ),
        affiliation=AffiliationContext(
            affiliation_id=affiliation_id,
            affiliation_type=AffiliationType.PERMANENT,
            department="security",
            roles=[REGISTRATION_RECOVERY_REVIEWER_ROLE],
            is_primary=True,
            valid_from=None,
            valid_until=None,
        ),
        session_binding=hashlib.sha256(token.encode()).hexdigest(),
    )


def _affiliation(provider: ProviderContext, **changes):
    row = MagicMock()
    row.id = provider.affiliation.affiliation_id
    row.provider_id = provider.provider.provider_id
    row.hospital_id = provider.hospital.hospital_id
    row.is_active = True
    row.trust_status = AffiliationTrustStatus.ACTIVE.value
    row.roles = [REGISTRATION_RECOVERY_REVIEWER_ROLE]
    row.valid_from = None
    row.valid_until = None
    for key, value in changes.items():
        setattr(row, key, value)
    return row


def _db(row):
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.mark.asyncio
async def test_reviewer_authority_requires_live_role_trust_session_and_recent_mfa(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "review-session"
    provider = _provider(token=token)
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        gate,
        "resolve_provider_session_context",
        AsyncMock(
            return_value={
                "authenticated": True,
                "provider_id": provider.actor_uid,
                "mfa_verified_at": (now - timedelta(minutes=2)).isoformat(),
            }
        ),
    )

    authority = await gate.authorize_registration_recovery_reviewer(
        _db(_affiliation(provider)),
        provider=provider,
        session_token=token,
        now=now,
    )

    assert authority.reviewer_id == provider.actor_uid
    assert authority.hospital_id == str(provider.hospital.hospital_id)
    assert authority.affiliation_id == str(provider.affiliation.affiliation_id)
    assert authority.authority_version == REGISTRATION_RECOVERY_REVIEW_AUTHORITY_VERSION
    assert authority.session_binding == hashlib.sha256(token.encode()).hexdigest()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "value", "expected"),
    [
        ("roles", [], "REGISTRATION_RECOVERY_REVIEW_ROLE_REQUIRED"),
        ("is_active", False, "REGISTRATION_RECOVERY_REVIEW_AFFILIATION_INVALID"),
        (
            "trust_status",
            AffiliationTrustStatus.SUSPENDED.value,
            "REGISTRATION_RECOVERY_REVIEW_AFFILIATION_INVALID",
        ),
    ],
)
async def test_reviewer_authority_rechecks_current_affiliation(
    monkeypatch: pytest.MonkeyPatch,
    change: str,
    value,
    expected: str,
) -> None:
    token = "review-session"
    provider = _provider(token=token)
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        gate,
        "resolve_provider_session_context",
        AsyncMock(
            return_value={
                "provider_id": provider.actor_uid,
                "mfa_verified_at": now.isoformat(),
            }
        ),
    )

    with pytest.raises(gate.RegistrationRecoveryReviewGateError) as exc_info:
        await gate.authorize_registration_recovery_reviewer(
            _db(_affiliation(provider, **{change: value})),
            provider=provider,
            session_token=token,
            now=now,
        )
    assert exc_info.value.code == expected


@pytest.mark.asyncio
async def test_reviewer_authority_rejects_expired_affiliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "review-session"
    provider = _provider(token=token)
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        gate,
        "resolve_provider_session_context",
        AsyncMock(
            return_value={
                "provider_id": provider.actor_uid,
                "mfa_verified_at": now.isoformat(),
            }
        ),
    )

    with pytest.raises(gate.RegistrationRecoveryReviewGateError) as exc_info:
        await gate.authorize_registration_recovery_reviewer(
            _db(_affiliation(provider, valid_until=now - timedelta(seconds=1))),
            provider=provider,
            session_token=token,
            now=now,
        )
    assert exc_info.value.code == "REGISTRATION_RECOVERY_REVIEW_AFFILIATION_INVALID"


@pytest.mark.asyncio
async def test_reviewer_authority_requires_recent_mfa(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "review-session"
    provider = _provider(token=token)
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        gate,
        "resolve_provider_session_context",
        AsyncMock(
            return_value={
                "provider_id": provider.actor_uid,
                "mfa_verified_at": (
                    now
                    - timedelta(
                        seconds=gate.REGISTRATION_RECOVERY_REVIEW_MFA_MAX_AGE_SECONDS + 1
                    )
                ).isoformat(),
            }
        ),
    )

    with pytest.raises(gate.RegistrationRecoveryReviewGateError) as exc_info:
        await gate.authorize_registration_recovery_reviewer(
            _db(_affiliation(provider)),
            provider=provider,
            session_token=token,
            now=now,
        )
    assert exc_info.value.code == "REGISTRATION_RECOVERY_REVIEW_MFA_REQUIRED"


@pytest.mark.asyncio
async def test_reviewer_authority_rejects_session_binding_or_subject_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(timezone.utc)
    provider = _provider(token="expected-session")

    with pytest.raises(gate.RegistrationRecoveryReviewGateError) as binding_error:
        await gate.authorize_registration_recovery_reviewer(
            _db(_affiliation(provider)),
            provider=provider,
            session_token="other-session",
            now=now,
        )
    assert (
        binding_error.value.code
        == "REGISTRATION_RECOVERY_REVIEW_SESSION_BINDING_MISMATCH"
    )

    monkeypatch.setattr(
        gate,
        "resolve_provider_session_context",
        AsyncMock(
            return_value={
                "provider_id": str(uuid.uuid4()),
                "mfa_verified_at": now.isoformat(),
            }
        ),
    )
    with pytest.raises(gate.RegistrationRecoveryReviewGateError) as subject_error:
        await gate.authorize_registration_recovery_reviewer(
            _db(_affiliation(provider)),
            provider=provider,
            session_token="expected-session",
            now=now,
        )
    assert (
        subject_error.value.code
        == "REGISTRATION_RECOVERY_REVIEW_SESSION_BINDING_MISMATCH"
    )
