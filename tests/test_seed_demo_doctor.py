from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.provider import (
    ProviderCredential,
    ProviderHospitalAffiliation,
    ProviderIdentity,
)
from app.services.provider_auth_service import normalize_provider_login_identifier
from scripts.seed_demo_doctor import (
    DEMO_PROVIDER_EMAIL,
    DEMO_PROVIDER_B_DISPLAY_NAME,
    DEMO_PROVIDER_B_EMAIL,
    DEMO_PROVIDER_B_MEDICAL_REGISTRATION_NUMBER,
    ProviderSeedResult,
    main,
    parse_args,
    require_demo_provider_b_password,
    require_demo_provider_password,
    seed_hospital,
    seed_provider,
    seed_provider_b,
)


STRONG_PASSWORD = "Alpha-Only-Strong-Password-42!"


def provider_row(*, active: bool = True) -> ProviderIdentity:
    provider = ProviderIdentity(
        display_name="Dr. Meera Joshi",
        medical_registration_number="MMC-2019-45231",
        contact_email=DEMO_PROVIDER_EMAIL,
        status="active" if active else "suspended",
        is_active=active,
    )
    provider.id = uuid.uuid4()
    return provider


def credential_row(
    provider: ProviderIdentity, password_hash: str = "existing-hash"
) -> ProviderCredential:
    credential = ProviderCredential(
        provider_id=provider.id,
        login_identifier=DEMO_PROVIDER_EMAIL,
        password_hash=password_hash,
        mfa_enabled=False,
        failed_login_attempts=0,
        is_active=True,
    )
    credential.id = uuid.uuid4()
    return credential


def affiliation_row(
    provider: ProviderIdentity, hospital_id: uuid.UUID
) -> ProviderHospitalAffiliation:
    affiliation = ProviderHospitalAffiliation(
        provider_id=provider.id,
        hospital_id=hospital_id,
        affiliation_type="permanent",
        roles=["clinician"],
        is_primary=True,
        is_active=True,
    )
    affiliation.id = uuid.uuid4()
    return affiliation


def provider_b_row(*, active: bool = True) -> ProviderIdentity:
    provider = ProviderIdentity(
        display_name=DEMO_PROVIDER_B_DISPLAY_NAME,
        medical_registration_number=DEMO_PROVIDER_B_MEDICAL_REGISTRATION_NUMBER,
        contact_email=DEMO_PROVIDER_B_EMAIL,
        role="clinician",
        status="active" if active else "suspended",
        is_active=active,
    )
    provider.id = uuid.uuid4()
    return provider


def credential_b_row(
    provider: ProviderIdentity, password_hash: str = "doctor-b-existing-hash"
) -> ProviderCredential:
    credential = ProviderCredential(
        provider_id=provider.id,
        login_identifier=DEMO_PROVIDER_B_EMAIL,
        password_hash=password_hash,
        mfa_enabled=False,
        failed_login_attempts=0,
        is_active=True,
    )
    credential.id = uuid.uuid4()
    return credential


def affiliation_b_row(
    provider: ProviderIdentity, hospital_id: uuid.UUID
) -> ProviderHospitalAffiliation:
    affiliation = ProviderHospitalAffiliation(
        provider_id=provider.id,
        hospital_id=hospital_id,
        affiliation_type="permanent",
        roles=["clinician"],
        is_primary=True,
        is_active=True,
    )
    affiliation.id = uuid.uuid4()
    return affiliation


def scalar_rows(rows):
    result = MagicMock()
    result.all.return_value = list(rows)
    return result


def fake_session(provider=None, credentials=None, affiliation=None):
    session = AsyncMock()
    session.add = MagicMock()

    def assign_id(row):
        if isinstance(
            row, (ProviderIdentity, ProviderCredential, ProviderHospitalAffiliation)
        ):
            if getattr(row, "id", None) is None:
                row.id = uuid.uuid4()

    session.add.side_effect = assign_id
    session.scalar.side_effect = [provider, affiliation]
    scalar_result = MagicMock()
    scalar_result.all.return_value = list(credentials or [])
    session.scalars.return_value = scalar_result
    return session


def fake_doctor_b_session(provider_candidates=None, credentials=None, affiliation=None):
    session = AsyncMock()
    session.add = MagicMock()

    def assign_id(row):
        if isinstance(
            row, (ProviderIdentity, ProviderCredential, ProviderHospitalAffiliation)
        ):
            if getattr(row, "id", None) is None:
                row.id = uuid.uuid4()

    session.add.side_effect = assign_id
    session.scalars.side_effect = [
        scalar_rows(provider_candidates or []),
        scalar_rows(credentials or []),
    ]
    session.scalar.return_value = affiliation
    return session


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, traceback):
        return False


@pytest.mark.asyncio
async def test_seeder_creates_missing_provider_identity_and_credential(monkeypatch):
    monkeypatch.setenv("DEMO_PROVIDER_PASSWORD", STRONG_PASSWORD)
    session = fake_session()
    hospital_id = uuid.uuid4()

    with patch(
        "scripts.seed_demo_doctor.hash_provider_password", return_value="new-hash"
    ):
        result = await seed_provider(session, hospital_id)

    added = [call.args[0] for call in session.add.call_args_list]
    assert result.provider_created is True
    assert result.credential_created is True
    assert any(isinstance(row, ProviderIdentity) for row in added)
    credential = next(row for row in added if isinstance(row, ProviderCredential))
    assert credential.password_hash == "new-hash"
    assert credential.login_identifier == DEMO_PROVIDER_EMAIL
    assert any(isinstance(row, ProviderHospitalAffiliation) for row in added)


@pytest.mark.asyncio
async def test_normal_seed_is_idempotent_and_does_not_overwrite_password(monkeypatch):
    monkeypatch.setenv("DEMO_PROVIDER_PASSWORD", "Different-Strong-Password-42!")
    provider = provider_row()
    credential = credential_row(provider)
    hospital_id = uuid.uuid4()
    affiliation = affiliation_row(provider, hospital_id)
    session = fake_session(provider, [credential], affiliation)

    with patch("scripts.seed_demo_doctor.hash_provider_password") as hash_password:
        result = await seed_provider(session, hospital_id)

    assert result.provider_created is False
    assert result.credential_created is False
    assert result.affiliation_created is False
    assert result.password_reset is False
    assert credential.password_hash == "existing-hash"
    hash_password.assert_not_called()
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_explicit_reset_updates_only_canonical_hash_and_security_state(
    monkeypatch,
):
    monkeypatch.setenv("DEMO_PROVIDER_PASSWORD", STRONG_PASSWORD)
    provider = provider_row()
    credential = credential_row(provider)
    credential.failed_login_attempts = 5
    credential.locked_until = datetime.now(timezone.utc) + timedelta(minutes=10)
    old_changed_at = datetime.now(timezone.utc) - timedelta(days=1)
    credential.password_changed_at = old_changed_at
    hospital_id = uuid.uuid4()
    session = fake_session(
        provider, [credential], affiliation_row(provider, hospital_id)
    )

    with patch(
        "scripts.seed_demo_doctor.hash_provider_password", return_value="rotated-hash"
    ):
        result = await seed_provider(session, hospital_id, reset_password=True)

    assert result.password_reset is True
    assert credential.password_hash == "rotated-hash"
    assert credential.failed_login_attempts == 0
    assert credential.locked_until is None
    assert credential.password_changed_at > old_changed_at


@pytest.mark.asyncio
async def test_reset_does_not_silently_reactivate_accounts(monkeypatch):
    monkeypatch.setenv("DEMO_PROVIDER_PASSWORD", STRONG_PASSWORD)
    provider = provider_row(active=False)
    credential = credential_row(provider)
    credential.is_active = False
    hospital_id = uuid.uuid4()
    session = fake_session(
        provider, [credential], affiliation_row(provider, hospital_id)
    )

    with patch(
        "scripts.seed_demo_doctor.hash_provider_password", return_value="rotated-hash"
    ):
        result = await seed_provider(session, hospital_id, reset_password=True)

    assert result.provider_active is False
    assert result.credential_active is False


@pytest.mark.asyncio
async def test_explicit_reactivation_requires_reset_and_changes_only_demo_rows(
    monkeypatch,
):
    monkeypatch.setenv("DEMO_PROVIDER_PASSWORD", STRONG_PASSWORD)
    provider = provider_row(active=False)
    credential = credential_row(provider)
    credential.is_active = False
    hospital_id = uuid.uuid4()
    session = fake_session(
        provider, [credential], affiliation_row(provider, hospital_id)
    )

    with patch(
        "scripts.seed_demo_doctor.hash_provider_password", return_value="rotated-hash"
    ):
        result = await seed_provider(
            session,
            hospital_id,
            reset_password=True,
            reactivate_provider=True,
            reactivate_credential=True,
        )

    assert result.provider_active is True
    assert result.credential_active is True


def test_reset_rejects_missing_environment_password(monkeypatch):
    monkeypatch.delenv("DEMO_PROVIDER_PASSWORD", raising=False)
    with pytest.raises(RuntimeError, match="Missing required"):
        require_demo_provider_password()


@pytest.mark.parametrize(
    "password",
    [
        "<GENERATE_A_STRONG_LOCAL_DEMO_PASSWORD>",
        "GENERATED_ALPHA_DEMO_PASSWORD",
        "password",
        "weak",
    ],
)
def test_reset_rejects_placeholder_or_weak_password(monkeypatch, password):
    monkeypatch.setenv("DEMO_PROVIDER_PASSWORD", password)
    with pytest.raises(RuntimeError):
        require_demo_provider_password()


def test_reset_requires_both_explicit_confirmation_flags():
    with pytest.raises(SystemExit):
        parse_args(["--reset-password"])
    with pytest.raises(SystemExit):
        parse_args(["--confirm-demo-provider-reset"])
    args = parse_args(["--reset-password", "--confirm-demo-provider-reset"])
    assert args.reset_password is True
    assert args.confirm_demo_provider_reset is True


@pytest.mark.asyncio
async def test_conflicting_credential_binding_fails_without_overwrite(monkeypatch):
    monkeypatch.setenv("DEMO_PROVIDER_PASSWORD", STRONG_PASSWORD)
    provider = provider_row()
    credential = credential_row(provider)
    credential.provider_id = uuid.uuid4()
    hospital_id = uuid.uuid4()
    session = fake_session(
        provider, [credential], affiliation_row(provider, hospital_id)
    )

    with pytest.raises(RuntimeError, match="different provider identity"):
        await seed_provider(session, hospital_id, reset_password=True)
    assert credential.password_hash == "existing-hash"


@pytest.mark.asyncio
async def test_doctor_a_and_b_are_distinct_and_share_only_the_hospital(monkeypatch):
    monkeypatch.setenv("DEMO_PROVIDER_PASSWORD", STRONG_PASSWORD)
    monkeypatch.setenv("DEMO_PROVIDER_B_PASSWORD", "Doctor-B-Strong-Password-84!")
    hospital_id = uuid.uuid4()
    doctor_a_session = fake_session()
    doctor_b_session = fake_doctor_b_session()

    with patch(
        "scripts.seed_demo_doctor.hash_provider_password",
        side_effect=["doctor-a-hash", "doctor-b-hash"],
    ):
        doctor_a_result = await seed_provider(doctor_a_session, hospital_id)
        doctor_b_result = await seed_provider_b(doctor_b_session, hospital_id)

    doctor_a_rows = [call.args[0] for call in doctor_a_session.add.call_args_list]
    doctor_b_rows = [call.args[0] for call in doctor_b_session.add.call_args_list]
    doctor_a = next(row for row in doctor_a_rows if isinstance(row, ProviderIdentity))
    doctor_b = next(row for row in doctor_b_rows if isinstance(row, ProviderIdentity))
    doctor_a_credential = next(
        row for row in doctor_a_rows if isinstance(row, ProviderCredential)
    )
    doctor_b_credential = next(
        row for row in doctor_b_rows if isinstance(row, ProviderCredential)
    )
    doctor_a_affiliation = next(
        row for row in doctor_a_rows if isinstance(row, ProviderHospitalAffiliation)
    )
    doctor_b_affiliation = next(
        row for row in doctor_b_rows if isinstance(row, ProviderHospitalAffiliation)
    )

    assert doctor_a_result.provider_id != doctor_b_result.provider_id
    assert doctor_a.medical_registration_number != doctor_b.medical_registration_number
    assert doctor_a_credential.login_identifier != doctor_b_credential.login_identifier
    assert normalize_provider_login_identifier(
        doctor_a_credential.login_identifier
    ) != normalize_provider_login_identifier(doctor_b_credential.login_identifier)
    assert doctor_a_credential.login_identifier == DEMO_PROVIDER_EMAIL
    assert doctor_b_credential.login_identifier == DEMO_PROVIDER_B_EMAIL
    assert doctor_a_affiliation.hospital_id == hospital_id
    assert doctor_b_affiliation.hospital_id == hospital_id
    assert doctor_b_affiliation.roles == ["clinician"]
    assert doctor_b.role == "clinician"
    assert doctor_b.is_active is True
    assert doctor_b_credential.is_active is True
    assert doctor_b_credential.mfa_enabled is False
    assert doctor_b_affiliation.is_active is True


@pytest.mark.asyncio
async def test_doctor_b_rerun_is_idempotent_and_preserves_doctor_a(monkeypatch):
    monkeypatch.setenv("DEMO_PROVIDER_B_PASSWORD", "Doctor-B-Strong-Password-84!")
    hospital_id = uuid.uuid4()
    doctor_b = provider_b_row()
    doctor_b_credential = credential_b_row(doctor_b)
    doctor_b_affiliation = affiliation_b_row(doctor_b, hospital_id)
    session = fake_doctor_b_session(
        [doctor_b], [doctor_b_credential], doctor_b_affiliation
    )

    doctor_a = provider_row()
    doctor_a_credential = credential_row(doctor_a)
    doctor_a_affiliation = affiliation_row(doctor_a, hospital_id)
    doctor_a_snapshot = (
        doctor_a.display_name,
        doctor_a.medical_registration_number,
        doctor_a.contact_email,
        doctor_a.status,
        doctor_a.is_active,
        doctor_a_credential.login_identifier,
        doctor_a_credential.password_hash,
        list(doctor_a_affiliation.roles),
        doctor_a_affiliation.is_active,
    )

    with patch("scripts.seed_demo_doctor.hash_provider_password") as hash_password:
        result = await seed_provider_b(session, hospital_id)

    assert result.provider_created is False
    assert result.credential_created is False
    assert result.affiliation_created is False
    assert result.password_reset is False
    session.add.assert_not_called()
    hash_password.assert_not_called()
    assert (
        doctor_a.display_name,
        doctor_a.medical_registration_number,
        doctor_a.contact_email,
        doctor_a.status,
        doctor_a.is_active,
        doctor_a_credential.login_identifier,
        doctor_a_credential.password_hash,
        list(doctor_a_affiliation.roles),
        doctor_a_affiliation.is_active,
    ) == doctor_a_snapshot


def test_doctor_b_rejects_missing_password(monkeypatch):
    monkeypatch.delenv("DEMO_PROVIDER_B_PASSWORD", raising=False)
    with pytest.raises(RuntimeError, match="DEMO_PROVIDER_B_PASSWORD"):
        require_demo_provider_b_password()


@pytest.mark.asyncio
async def test_doctor_b_requires_existing_demo_hospital():
    session = AsyncMock()
    session.add = MagicMock()
    session.scalar.return_value = None

    with pytest.raises(RuntimeError, match="seed Doctor A before Doctor B"):
        await seed_hospital(session, require_existing=True)

    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.parametrize(
    "password",
    [
        "<GENERATE_A_STRONG_LOCAL_DEMO_PASSWORD>",
        "GENERATED_ALPHA_DEMO_PASSWORD",
        "password",
        "DoctorBPassword",
        "doctor-b-weak-42",
    ],
)
def test_doctor_b_rejects_placeholder_or_weak_password(monkeypatch, password):
    monkeypatch.setenv("DEMO_PROVIDER_B_PASSWORD", password)
    with pytest.raises(RuntimeError, match="DEMO_PROVIDER_B_PASSWORD"):
        require_demo_provider_b_password()


def test_doctor_b_cli_rejects_doctor_a_reset_flags():
    args = parse_args(["--doctor-b-only"])
    assert args.doctor_b_only is True
    with pytest.raises(SystemExit):
        parse_args(
            [
                "--doctor-b-only",
                "--reset-password",
                "--confirm-demo-provider-reset",
            ]
        )


@pytest.mark.asyncio
async def test_doctor_b_conflicting_credential_ownership_rolls_back(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("DEMO_PROVIDER_B_PASSWORD", "Doctor-B-Strong-Password-84!")
    hospital_id = uuid.uuid4()
    doctor_b = provider_b_row()
    doctor_a = provider_row()
    conflicting_credential = credential_b_row(doctor_a)
    original_hash = conflicting_credential.password_hash
    session = fake_doctor_b_session([doctor_b], [conflicting_credential])
    session_factory = MagicMock(return_value=FakeSessionContext(session))

    with (
        patch(
            "scripts.seed_demo_doctor.get_session_factory", return_value=session_factory
        ),
        patch(
            "scripts.seed_demo_doctor.seed_hospital",
            new=AsyncMock(return_value=hospital_id),
        ),
    ):
        with pytest.raises(RuntimeError, match="different provider identity"):
            await main(["--doctor-b-only"])

    assert conflicting_credential.provider_id == doctor_a.id
    assert conflicting_credential.password_hash == original_hash
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()


def test_no_seed_output_statement_contains_password_or_hash():
    source = open("scripts/seed_demo_doctor.py", encoding="utf-8").read()
    print_lines = [line for line in source.splitlines() if "print(" in line]
    assert not any("DEMO_PROVIDER_PASSWORD" in line for line in print_lines)
    assert not any("password_hash" in line for line in print_lines)


@pytest.mark.asyncio
async def test_doctor_b_cli_is_provider_only_and_output_is_safe(monkeypatch, capsys):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ENV", "development")
    secret = "Doctor-B-Strong-Password-84!"
    monkeypatch.setenv("DEMO_PROVIDER_B_PASSWORD", secret)
    session = fake_doctor_b_session()
    session_factory = MagicMock(return_value=FakeSessionContext(session))
    hospital_id = uuid.uuid4()
    password_hash = "doctor-b-test-password-hash"

    with (
        patch(
            "scripts.seed_demo_doctor.get_session_factory", return_value=session_factory
        ),
        patch(
            "scripts.seed_demo_doctor.seed_hospital",
            new=AsyncMock(return_value=hospital_id),
        ) as seed_demo_hospital,
        patch(
            "scripts.seed_demo_doctor.hash_provider_password",
            return_value=password_hash,
        ),
        patch("scripts.seed_demo_doctor.seed_provider", new=AsyncMock()) as doctor_a,
        patch("scripts.seed_demo_doctor.seed_nfc_card", new=AsyncMock()) as seed_nfc,
        patch(
            "scripts.seed_demo_doctor.seed_clinical_records", new=AsyncMock()
        ) as seed_clinical,
    ):
        exit_code = await main(["--doctor-b-only"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert DEMO_PROVIDER_B_DISPLAY_NAME in output
    assert DEMO_PROVIDER_B_EMAIL in output
    assert "provider=created" in output
    assert "credential=created" in output
    assert "affiliation=created" in output
    added = [call.args[0] for call in session.add.call_args_list]
    provider_b = next(row for row in added if isinstance(row, ProviderIdentity))
    credential_b = next(row for row in added if isinstance(row, ProviderCredential))
    assert credential_b.password_hash == password_hash
    assert secret not in output
    assert password_hash not in output
    assert str(provider_b.id) not in output
    assert str(hospital_id) not in output
    assert "provider_id" not in output
    assert "hospital_id" not in output
    assert "hash" not in output.lower()
    doctor_a.assert_not_awaited()
    seed_nfc.assert_not_awaited()
    seed_clinical.assert_not_awaited()
    seed_demo_hospital.assert_awaited_once_with(session, require_existing=True)
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_main_reset_revokes_sessions_and_writes_audit(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ENV", "development")
    session = AsyncMock()
    session_factory = MagicMock(return_value=FakeSessionContext(session))
    provider_id = uuid.uuid4()
    hospital_id = uuid.uuid4()
    result = ProviderSeedResult(
        provider_id=provider_id,
        provider_created=False,
        credential_created=False,
        affiliation_created=False,
        password_reset=True,
        provider_active=True,
        credential_active=True,
    )

    with (
        patch(
            "scripts.seed_demo_doctor.get_session_factory", return_value=session_factory
        ),
        patch(
            "scripts.seed_demo_doctor.seed_hospital",
            new=AsyncMock(return_value=hospital_id),
        ),
        patch(
            "scripts.seed_demo_doctor.seed_provider", new=AsyncMock(return_value=result)
        ),
        patch("scripts.seed_demo_doctor.seed_nfc_card", new=AsyncMock()),
        patch("scripts.seed_demo_doctor.seed_clinical_records", new=AsyncMock()),
        patch(
            "scripts.seed_demo_doctor.revoke_provider_auth_sessions",
            new=AsyncMock(return_value=2),
        ) as revoke,
        patch(
            "scripts.seed_demo_doctor.append_audit_log",
            new=AsyncMock(return_value=True),
        ) as audit,
    ):
        exit_code = await main(["--reset-password", "--confirm-demo-provider-reset"])

    assert exit_code == 0
    revoke.assert_awaited_once_with(provider_id)
    assert audit.await_args.kwargs["event_type"] == "PROVIDER_PASSWORD_RESET"
    audit_context = audit.await_args.kwargs["audit_context"]
    assert audit_context.hospital_id == str(hospital_id)
    assert audit_context.domain.value == "auth"
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_main_reset_rolls_back_when_audit_fails(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ENV", "development")
    session = AsyncMock()
    session_factory = MagicMock(return_value=FakeSessionContext(session))
    result = ProviderSeedResult(
        provider_id=uuid.uuid4(),
        provider_created=False,
        credential_created=False,
        affiliation_created=False,
        password_reset=True,
        provider_active=True,
        credential_active=True,
    )

    with (
        patch(
            "scripts.seed_demo_doctor.get_session_factory", return_value=session_factory
        ),
        patch(
            "scripts.seed_demo_doctor.seed_hospital",
            new=AsyncMock(return_value=uuid.uuid4()),
        ),
        patch(
            "scripts.seed_demo_doctor.seed_provider", new=AsyncMock(return_value=result)
        ),
        patch(
            "scripts.seed_demo_doctor.revoke_provider_auth_sessions",
            new=AsyncMock(return_value=1),
        ),
        patch(
            "scripts.seed_demo_doctor.append_audit_log",
            new=AsyncMock(return_value=False),
        ),
    ):
        with pytest.raises(RuntimeError, match="Audit write failed"):
            await main(["--reset-password", "--confirm-demo-provider-reset"])

    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
