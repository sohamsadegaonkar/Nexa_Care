from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.security import encrypt_mfa_secret
from app.models.patient import Patient
from app.models.patient_auth_identity import PatientAuthIdentity
from app.models.provider import (
    AffiliationTrustStatus,
    FacilityVerification,
    FacilityVerificationStatus,
    ProviderCredential,
    ProviderHospitalAffiliation,
    ProviderIdentity,
    ProviderTrustPermissionGrant,
    ProfessionalVerification,
    ProfessionalVerificationStatus,
    HospitalRegistry,
)
from scripts.seed_demo_doctor import (
    DEMO_PATIENT_1_ID,
    DEMO_PATIENT_2_ID,
    DEMO_PROVIDER_EMAIL,
    HospitalSeedResult,
    ProviderSeedResult,
    ROOT,
    load_standalone_demo_env,
    main,
    parse_args,
    require_demo_provider_password,
    require_demo_provider_totp_secret,
    require_disposable_demo_target,
    seed_authoritative_patients,
    seed_clinical_records,
    seed_demo_clinical_trust,
    seed_hospital,
    seed_local_demo_patient_auth_identities,
    seed_nfc_card,
    seed_provider,
)
from app.services.local_demo_patient_auth import (
    LOCAL_DEMO_PATIENT_AUTH_PROVIDER,
    local_demo_patient_subject,
)


STRONG_PASSWORD = "Alpha-Only-Strong-Password-42!"
DEMO_TOTP_SECRET = "JBSWY3DPEHPK3PXP"


@pytest.fixture(autouse=True)
def demo_totp_secret(monkeypatch):
    monkeypatch.setenv("DEMO_PROVIDER_TOTP_SECRET", DEMO_TOTP_SECRET)


def provider_row(*, active: bool = True) -> ProviderIdentity:
    verified_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    provider = ProviderIdentity(
        display_name="Dr. Meera Joshi",
        medical_registration_number="MMC-2019-45231",
        specialty="Internal Medicine",
        contact_email=DEMO_PROVIDER_EMAIL,
        contact_phone="+91 98765 00001",
        email_verified_at=verified_at,
        phone_verified_at=verified_at,
        status="active" if active else "suspended",
        is_active=active,
        role="provider",
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
        mfa_enabled=True,
        mfa_secret_encrypted=encrypt_mfa_secret(DEMO_TOTP_SECRET),
        failed_login_attempts=0,
        is_active=True,
        provider_uid=None,
        mfa_secret=None,
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
        department="Internal Medicine",
        roles=["clinician"],
        is_primary=True,
        is_active=True,
        trust_status=AffiliationTrustStatus.ACTIVE.value,
        valid_from=datetime.now(timezone.utc) - timedelta(minutes=1),
        valid_until=datetime.now(timezone.utc) + timedelta(days=1),
    )
    affiliation.id = uuid.uuid4()
    return affiliation


def professional_verification_row(provider: ProviderIdentity) -> ProfessionalVerification:
    now = datetime.now(timezone.utc)
    return ProfessionalVerification(
        provider_id=provider.id,
        registration_authority_code="NEXA-DEMO",
        registration_number_normalized="NEXA-DEMO-MEERA-001",
        status=ProfessionalVerificationStatus.VERIFIED.value,
        verification_method="synthetic-demo-seed",
        verification_source="local-disposable-demo",
        verification_reference="nexa-demo-professional-20260922",
        registration_valid_from=now - timedelta(minutes=1),
        registration_valid_until=now + timedelta(days=1),
        verified_at=now - timedelta(minutes=1),
        last_checked_at=now - timedelta(minutes=1),
        next_review_at=now + timedelta(hours=12),
        previous_verification_valid=True,
        reviewer_id="DEMO_SEEDER",
        decision_reason_code="SYNTHETIC_DEMO_SEED",
        version=1,
    )


def facility_verification_row(hospital_id: uuid.UUID) -> FacilityVerification:
    now = datetime.now(timezone.utc)
    return FacilityVerification(
        facility_id=hospital_id,
        status=FacilityVerificationStatus.VERIFIED.value,
        verification_method="synthetic-demo-seed",
        verification_source="local-disposable-demo",
        verification_reference="nexa-demo-facility-20260922",
        registration_authority_code="NEXA-DEMO",
        registration_number_normalized="NEXA-DEMO-HOSPITAL-001",
        registration_valid_from=now - timedelta(minutes=1),
        registration_valid_until=now + timedelta(days=1),
        verified_at=now - timedelta(minutes=1),
        last_checked_at=now - timedelta(minutes=1),
        next_review_at=now + timedelta(hours=12),
        previous_verification_valid=True,
        reviewer_id="DEMO_SEEDER",
        decision_reason_code="SYNTHETIC_DEMO_SEED",
        version=1,
    )


def hospital_row(hospital_id: uuid.UUID) -> HospitalRegistry:
    return HospitalRegistry(
        id=hospital_id,
        facility_code="NEXA-DEMO-HOSPITAL",
        legal_name="Nexa Care Demo Hospital Pvt. Ltd.",
        display_name="Nexa Demo Hospital",
        city="Mumbai",
        state="MH",
        country_code="IN",
        is_active=True,
    )


def trust_session(
    provider: ProviderIdentity,
    hospital: HospitalRegistry,
    affiliation: ProviderHospitalAffiliation,
    professional: ProfessionalVerification,
    facility: FacilityVerification,
):
    session = AsyncMock()
    session.add = MagicMock()
    session.get.side_effect = [provider, hospital]
    session.scalar.side_effect = [affiliation, professional, facility]
    return session


def scalar_rows(rows):
    result = MagicMock()
    result.all.return_value = list(rows)
    return result


def fake_session(
    provider=None,
    credentials=None,
    affiliation=None,
    *,
    providers=None,
    affiliations=None,
    trust_grants=None,
):
    session = AsyncMock()
    session.add = MagicMock()

    def assign_id(row):
        if isinstance(
            row, (ProviderIdentity, ProviderCredential, ProviderHospitalAffiliation)
        ):
            if getattr(row, "id", None) is None:
                row.id = uuid.uuid4()

    session.add.side_effect = assign_id
    provider_rows = list(providers) if providers is not None else (
        [provider] if provider is not None else []
    )
    affiliation_rows = (
        list(affiliations)
        if affiliations is not None
        else ([affiliation] if affiliation is not None else [])
    )
    session.scalars.side_effect = [
        scalar_rows(provider_rows),
        scalar_rows(trust_grants or []),
        scalar_rows(credentials or []),
        scalar_rows(affiliation_rows),
    ]
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
    assert credential.mfa_enabled is True
    assert credential.mfa_secret_encrypted
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

    with pytest.raises(RuntimeError, match="credential is inactive"):
        await seed_provider(session, hospital_id, reset_password=True)

    assert provider.is_active is False
    assert credential.is_active is False


@pytest.mark.asyncio
async def test_routine_seed_refuses_changed_credential_mfa_configuration(monkeypatch):
    monkeypatch.setenv("DEMO_PROVIDER_PASSWORD", STRONG_PASSWORD)
    provider = provider_row()
    credential = credential_row(provider)
    credential.mfa_enabled = False
    credential.mfa_secret_encrypted = None
    hospital_id = uuid.uuid4()
    session = fake_session(
        provider, [credential], affiliation_row(provider, hospital_id)
    )

    with pytest.raises(RuntimeError, match="MFA configuration changed"):
        await seed_provider(session, hospital_id)

    assert credential.mfa_enabled is False
    assert credential.mfa_secret_encrypted is None


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


def test_seed_rejects_missing_totp_secret(monkeypatch):
    monkeypatch.delenv("DEMO_PROVIDER_TOTP_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="DEMO_PROVIDER_TOTP_SECRET"):
        require_demo_provider_totp_secret()


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


def test_reactivation_requires_both_provider_and_credential_flags():
    with pytest.raises(SystemExit):
        parse_args(
            [
                "--reset-password",
                "--confirm-demo-provider-reset",
                "--reactivate-provider",
            ]
        )
    with pytest.raises(SystemExit):
        parse_args(
            [
                "--reset-password",
                "--confirm-demo-provider-reset",
                "--reactivate-credential",
            ]
        )


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


def test_no_seed_output_statement_contains_password_or_hash():
    source = open("scripts/seed_demo_doctor.py", encoding="utf-8").read()
    print_lines = [line for line in source.splitlines() if "print(" in line]
    assert not any("DEMO_PROVIDER_PASSWORD" in line for line in print_lines)
    assert not any("DEMO_PROVIDER_TOTP_SECRET" in line for line in print_lines)
    assert not any("password_hash" in line for line in print_lines)
    assert not any("mfa_secret_encrypted" in line for line in print_lines)


def test_explicit_database_url_skips_repository_env_file(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://demo@localhost/nexa_demo")
    monkeypatch.delenv("NEXA_DEMO_ENV_FILE", raising=False)

    with patch("scripts.seed_demo_doctor.load_dotenv") as load:
        load_standalone_demo_env()

    load.assert_not_called()


def test_absent_explicit_target_uses_non_overriding_disposable_demo_fallback(
    monkeypatch,
):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("NEXA_DEMO_ENV_FILE", raising=False)

    with patch("scripts.seed_demo_doctor.load_dotenv") as load:
        with patch.object(Path, "is_file", return_value=True):
            load_standalone_demo_env()

    load.assert_called_once_with(ROOT / ".env.demo.local", override=False)


def test_explicit_demo_env_file_is_loaded_without_overriding_process_values(
    monkeypatch, tmp_path
):
    env_file = tmp_path / "local-demo.env"
    env_file.write_text(
        "DATABASE_URL=postgresql+asyncpg://file@localhost/nexa_demo\n"
        "NEXA_DEMO_ENV_FILE_LOADED=true\n"
    )
    monkeypatch.setenv("NEXA_DEMO_ENV_FILE", str(env_file))
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://process@localhost/nexa_demo"
    )

    load_standalone_demo_env()

    assert (
        os.environ["DATABASE_URL"] == "postgresql+asyncpg://process@localhost/nexa_demo"
    )
    assert os.environ["NEXA_DEMO_ENV_FILE_LOADED"] == "true"


def test_explicit_demo_env_file_never_falls_back_to_repository_env(monkeypatch):
    monkeypatch.setenv("NEXA_DEMO_ENV_FILE", "does-not-exist.env")

    with patch("scripts.seed_demo_doctor.load_dotenv") as load:
        with pytest.raises(RuntimeError, match="NEXA_DEMO_ENV_FILE"):
            load_standalone_demo_env()

    load.assert_not_called()


def test_seed_target_requires_explicit_loopback_disposable_development_url(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://demo@127.0.0.1/nexa_qual_demo_20260922",
    )

    require_disposable_demo_target()

    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://demo@db.example.test/nexa_qual_demo_20260922",
    )
    with pytest.raises(RuntimeError, match="loopback"):
        require_disposable_demo_target()


@pytest.mark.asyncio
async def test_seeder_refuses_to_reuse_a_non_synthetic_hospital_code():
    hospital = HospitalRegistry(
        facility_code="NEXA-DEMO-HOSPITAL",
        legal_name="Another facility",
        display_name="Another facility",
        country_code="IN",
        is_active=True,
    )
    session = AsyncMock()
    session.add = MagicMock()
    session.scalar.return_value = hospital

    with pytest.raises(RuntimeError, match="non-synthetic facility"):
        await seed_hospital(session)

    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_seeder_refuses_to_reuse_a_non_synthetic_provider_login(monkeypatch):
    monkeypatch.setenv("DEMO_PROVIDER_PASSWORD", STRONG_PASSWORD)
    provider = provider_row()
    provider.specialty = "Different specialty"
    session = fake_session(provider=provider)

    with pytest.raises(RuntimeError, match="non-synthetic provider"):
        await seed_provider(session, uuid.uuid4())

    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_seeder_refuses_ambiguous_case_normalized_provider_logins(monkeypatch):
    monkeypatch.setenv("DEMO_PROVIDER_PASSWORD", STRONG_PASSWORD)
    primary = provider_row()
    duplicate = provider_row()
    duplicate.id = uuid.uuid4()
    duplicate.contact_email = "Demo.Doctor@nexacare.in"
    session = fake_session(providers=[primary, duplicate])

    with pytest.raises(RuntimeError, match="Multiple provider identities"):
        await seed_provider(session, uuid.uuid4())

    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_seeder_refuses_demo_provider_with_active_trust_management_grant(
    monkeypatch,
):
    monkeypatch.setenv("DEMO_PROVIDER_PASSWORD", STRONG_PASSWORD)
    provider = provider_row()
    hospital_id = uuid.uuid4()
    session = fake_session(
        provider,
        [credential_row(provider)],
        affiliation_row(provider, hospital_id),
        trust_grants=[MagicMock(spec=ProviderTrustPermissionGrant)],
    )

    with pytest.raises(RuntimeError, match="active trust-management grant"):
        await seed_provider(session, hospital_id)

    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_seeder_refuses_demo_provider_with_extra_affiliation(monkeypatch):
    monkeypatch.setenv("DEMO_PROVIDER_PASSWORD", STRONG_PASSWORD)
    provider = provider_row()
    hospital_id = uuid.uuid4()
    session = fake_session(
        provider,
        [credential_row(provider)],
        affiliations=[
            affiliation_row(provider, hospital_id),
            affiliation_row(provider, uuid.uuid4()),
        ],
    )

    with pytest.raises(RuntimeError, match="multiple affiliations"):
        await seed_provider(session, hospital_id)

    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_routine_seed_refuses_to_recreate_an_existing_provider_affiliation(
    monkeypatch,
):
    monkeypatch.setenv("DEMO_PROVIDER_PASSWORD", STRONG_PASSWORD)
    provider = provider_row()
    credential = credential_row(provider)
    session = fake_session(provider, [credential], affiliation=None)

    with pytest.raises(RuntimeError, match="missing its affiliation"):
        await seed_provider(session, uuid.uuid4())

    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_routine_seed_refuses_to_recreate_a_missing_existing_credential(
    monkeypatch,
):
    monkeypatch.setenv("DEMO_PROVIDER_PASSWORD", STRONG_PASSWORD)
    provider = provider_row()
    session = fake_session(
        provider,
        credentials=[],
        affiliation=affiliation_row(provider, uuid.uuid4()),
    )

    with pytest.raises(RuntimeError, match="missing its credential"):
        await seed_provider(session, uuid.uuid4())

    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_routine_seed_refuses_to_normalize_an_existing_credential_binding(
    monkeypatch,
):
    monkeypatch.setenv("DEMO_PROVIDER_PASSWORD", STRONG_PASSWORD)
    provider = provider_row()
    credential = credential_row(provider)
    credential.login_identifier = "Demo.Doctor@nexacare.in"
    session = fake_session(
        provider,
        [credential],
        affiliation_row(provider, uuid.uuid4()),
    )

    with pytest.raises(RuntimeError, match="login binding changed"):
        await seed_provider(session, uuid.uuid4())

    assert credential.login_identifier == "Demo.Doctor@nexacare.in"


@pytest.mark.asyncio
async def test_explicit_full_reactivation_may_restore_missing_demo_affiliation(
    monkeypatch,
):
    monkeypatch.setenv("DEMO_PROVIDER_PASSWORD", STRONG_PASSWORD)
    provider = provider_row()
    credential = credential_row(provider)
    session = fake_session(provider, [credential], affiliation=None)

    result = await seed_provider(
        session,
        uuid.uuid4(),
        reset_password=True,
        reactivate_provider=True,
        reactivate_credential=True,
    )

    assert result.affiliation_created is True
    assert any(
        isinstance(call.args[0], ProviderHospitalAffiliation)
        for call in session.add.call_args_list
    )


@pytest.mark.asyncio
async def test_routine_seed_never_reactivates_a_disabled_demo_provider():
    provider = provider_row(active=False)
    hospital_id = uuid.uuid4()
    hospital = HospitalRegistry(
        id=hospital_id,
        facility_code="NEXA-DEMO-HOSPITAL",
        legal_name="Nexa Care Demo Hospital Pvt. Ltd.",
        display_name="Nexa Demo Hospital",
        city="Mumbai",
        state="MH",
        country_code="IN",
        is_active=True,
    )
    session = AsyncMock()
    session.add = MagicMock()
    session.get.side_effect = [provider, hospital]
    session.scalar.return_value = affiliation_row(provider, hospital_id)

    with pytest.raises(RuntimeError, match="explicit reset and reactivation"):
        await seed_demo_clinical_trust(session, hospital_id, provider.id)

    assert provider.is_active is False
    assert provider.status == "suspended"
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_routine_seed_refuses_missing_existing_professional_verification():
    provider = provider_row()
    hospital_id = uuid.uuid4()
    hospital = HospitalRegistry(
        id=hospital_id,
        facility_code="NEXA-DEMO-HOSPITAL",
        legal_name="Nexa Care Demo Hospital Pvt. Ltd.",
        display_name="Nexa Demo Hospital",
        city="Mumbai",
        state="MH",
        country_code="IN",
        is_active=True,
    )
    session = AsyncMock()
    session.add = MagicMock()
    session.get.side_effect = [provider, hospital]
    session.scalar.side_effect = [affiliation_row(provider, hospital_id), None]

    with pytest.raises(RuntimeError, match="missing professional verification"):
        await seed_demo_clinical_trust(session, hospital_id, provider.id)

    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_routine_seed_refuses_missing_existing_facility_verification():
    provider = provider_row()
    hospital_id = uuid.uuid4()
    hospital = HospitalRegistry(
        id=hospital_id,
        facility_code="NEXA-DEMO-HOSPITAL",
        legal_name="Nexa Care Demo Hospital Pvt. Ltd.",
        display_name="Nexa Demo Hospital",
        city="Mumbai",
        state="MH",
        country_code="IN",
        is_active=True,
    )
    session = AsyncMock()
    session.add = MagicMock()
    session.get.side_effect = [provider, hospital]
    session.scalar.side_effect = [
        affiliation_row(provider, hospital_id),
        professional_verification_row(provider),
        None,
    ]

    with pytest.raises(RuntimeError, match="missing facility verification"):
        await seed_demo_clinical_trust(session, hospital_id, provider.id)

    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_seeder_creates_minimum_synthetic_clinical_trust_records():
    provider = provider_row()
    hospital_id = uuid.uuid4()
    hospital = HospitalRegistry(
        id=hospital_id,
        facility_code="NEXA-DEMO-HOSPITAL",
        legal_name="Nexa Care Demo Hospital Pvt. Ltd.",
        display_name="Nexa Demo Hospital",
        city="Mumbai",
        state="MH",
        country_code="IN",
        is_active=True,
    )
    affiliation = affiliation_row(provider, hospital_id)
    session = AsyncMock()
    session.add = MagicMock()
    session.get.side_effect = [provider, hospital]
    session.scalar.side_effect = [affiliation, None, None]

    await seed_demo_clinical_trust(
        session,
        hospital_id,
        provider.id,
        provider_created=True,
        hospital_created=True,
    )

    added = [call.args[0] for call in session.add.call_args_list]
    professional = next(
        row for row in added if isinstance(row, ProfessionalVerification)
    )
    facility = next(row for row in added if isinstance(row, FacilityVerification))
    assert professional.status == ProfessionalVerificationStatus.VERIFIED.value
    assert professional.verification_source == "local-disposable-demo"
    assert facility.status == FacilityVerificationStatus.VERIFIED.value
    assert facility.verification_source == "local-disposable-demo"
    assert provider.email_verified_at is not None
    assert provider.phone_verified_at is not None
    assert affiliation.roles == ["clinician"]
    assert affiliation.trust_status == AffiliationTrustStatus.ACTIVE.value
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target", "field", "value_factory"),
    [
        ("affiliation", "valid_from", lambda now: now + timedelta(minutes=1)),
        (
            "professional",
            "registration_valid_from",
            lambda now: now + timedelta(minutes=1),
        ),
        ("professional", "verified_at", lambda _now: None),
        ("professional", "next_review_at", lambda now: now - timedelta(minutes=1)),
        ("facility", "verified_at", lambda _now: None),
        ("facility", "next_review_at", lambda now: now - timedelta(minutes=1)),
    ],
)
async def test_routine_seed_refuses_trust_fixture_that_clinical_eligibility_denies(
    target,
    field,
    value_factory,
):
    provider = provider_row()
    hospital_id = uuid.uuid4()
    hospital = hospital_row(hospital_id)
    affiliation = affiliation_row(provider, hospital_id)
    professional = professional_verification_row(provider)
    facility = facility_verification_row(hospital_id)
    setattr(
        {"affiliation": affiliation, "professional": professional, "facility": facility}[target],
        field,
        value_factory(datetime.now(timezone.utc)),
    )
    session = trust_session(provider, hospital, affiliation, professional, facility)

    with pytest.raises(RuntimeError, match="changed or expired"):
        await seed_demo_clinical_trust(session, hospital_id, provider.id)

    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_routine_seed_refuses_future_contact_assurance_timestamp():
    provider = provider_row()
    provider.email_verified_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    hospital_id = uuid.uuid4()
    hospital = hospital_row(hospital_id)
    session = trust_session(
        provider,
        hospital,
        affiliation_row(provider, hospital_id),
        professional_verification_row(provider),
        facility_verification_row(hospital_id),
    )

    with pytest.raises(RuntimeError, match="contact assurance changed"):
        await seed_demo_clinical_trust(session, hospital_id, provider.id)

    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_seeder_creates_both_authoritative_synthetic_patients():
    session = AsyncMock()
    session.add = MagicMock()
    session.get.side_effect = [None, None]

    await seed_authoritative_patients(session)

    patients = [call.args[0] for call in session.add.call_args_list]
    assert {patient.patient_uuid for patient in patients} == {
        DEMO_PATIENT_1_ID,
        DEMO_PATIENT_2_ID,
    }
    assert all(
        patient.is_deleted is False and patient.dek_id is None for patient in patients
    )
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_seeder_reuses_existing_active_authoritative_patients():
    aarav = Patient(
        patient_uuid=DEMO_PATIENT_1_ID,
        is_deleted=False,
        dek_id=None,
        consent_assurance_policy="STANDARD",
    )
    priya = Patient(
        patient_uuid=DEMO_PATIENT_2_ID,
        is_deleted=False,
        dek_id=None,
        consent_assurance_policy="STANDARD",
    )
    session = AsyncMock()
    session.add = MagicMock()
    session.get.side_effect = [aarav, priya]

    await seed_authoritative_patients(session)

    session.add.assert_not_called()
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_seeder_refuses_to_reuse_soft_deleted_canonical_patient():
    session = AsyncMock()
    session.add = MagicMock()
    session.get.side_effect = [
        Patient(patient_uuid=DEMO_PATIENT_1_ID, is_deleted=True),
    ]

    with pytest.raises(RuntimeError, match="soft-deleted"):
        await seed_authoritative_patients(session)

    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "patient_kwargs",
    [
        {"dek_id": "unexpected-dek", "consent_assurance_policy": "STANDARD"},
        {"dek_id": None, "consent_assurance_policy": "ELEVATED"},
    ],
)
async def test_seeder_refuses_patient_with_non_synthetic_security_state(patient_kwargs):
    session = AsyncMock()
    session.add = MagicMock()
    session.get.side_effect = [
        Patient(
            patient_uuid=DEMO_PATIENT_1_ID,
            is_deleted=False,
            **patient_kwargs,
        )
    ]

    with pytest.raises(RuntimeError, match="non-synthetic security state"):
        await seed_authoritative_patients(session)

    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_seeder_creates_only_closed_local_demo_patient_auth_identities():
    session = AsyncMock()
    session.add = MagicMock()
    session.scalars.side_effect = [scalar_rows([]), scalar_rows([])]

    await seed_local_demo_patient_auth_identities(
        session,
        newly_created_patient_ids=frozenset({DEMO_PATIENT_1_ID, DEMO_PATIENT_2_ID}),
    )

    identities = [call.args[0] for call in session.add.call_args_list]
    assert {
        (identity.patient_id, identity.provider_subject) for identity in identities
    } == {
        (DEMO_PATIENT_1_ID, local_demo_patient_subject("aarav")),
        (DEMO_PATIENT_2_ID, local_demo_patient_subject("priya")),
    }
    assert all(
        identity.provider == LOCAL_DEMO_PATIENT_AUTH_PROVIDER for identity in identities
    )
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_seeder_refuses_rebound_or_revoked_local_demo_identity():
    identity = PatientAuthIdentity(
        patient_id=uuid.uuid4(),
        provider=LOCAL_DEMO_PATIENT_AUTH_PROVIDER,
        provider_subject=local_demo_patient_subject("aarav"),
    )
    session = AsyncMock()
    session.add = MagicMock()
    session.scalars.side_effect = [scalar_rows([identity])]

    with pytest.raises(RuntimeError, match="different patient"):
        await seed_local_demo_patient_auth_identities(session)

    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_seeder_refuses_to_attach_a_new_demo_identity_to_an_existing_patient():
    session = AsyncMock()
    session.add = MagicMock()
    session.scalars.side_effect = [scalar_rows([])]

    with pytest.raises(RuntimeError, match="missing its local authentication identity"):
        await seed_local_demo_patient_auth_identities(session)

    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_seeder_refuses_extra_patient_authentication_identity():
    expected = PatientAuthIdentity(
        patient_id=DEMO_PATIENT_1_ID,
        provider=LOCAL_DEMO_PATIENT_AUTH_PROVIDER,
        provider_subject=local_demo_patient_subject("aarav"),
    )
    unexpected = PatientAuthIdentity(
        patient_id=DEMO_PATIENT_1_ID,
        provider="other_provider",
        provider_subject="other-subject",
    )
    session = AsyncMock()
    session.add = MagicMock()
    session.scalars.side_effect = [scalar_rows([expected, unexpected])]

    with pytest.raises(RuntimeError, match="unexpected additional"):
        await seed_local_demo_patient_auth_identities(session)

    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_seeder_refuses_to_reassign_an_existing_nfc_uid():
    existing_card = MagicMock(
        patient_id=uuid.uuid4(), issued_by=uuid.uuid4(), status="active"
    )
    session = AsyncMock()
    session.add = MagicMock()
    session.scalar.return_value = existing_card

    with pytest.raises(RuntimeError, match="already bound to another identity"):
        await seed_nfc_card(session, DEMO_PATIENT_1_ID, uuid.uuid4())

    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_routine_seed_refuses_to_recreate_missing_existing_nfc_card():
    session = AsyncMock()
    session.add = MagicMock()
    session.scalar.return_value = None

    with pytest.raises(RuntimeError, match="missing its NFC card"):
        await seed_nfc_card(session, DEMO_PATIENT_1_ID, uuid.uuid4())

    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_seed_creates_an_nfc_card_only_for_a_new_demo_patient():
    provider_id = uuid.uuid4()
    session = AsyncMock()
    session.add = MagicMock()
    session.scalar.return_value = None

    await seed_nfc_card(
        session,
        DEMO_PATIENT_1_ID,
        provider_id,
        patient_created=True,
    )

    card = session.add.call_args.args[0]
    assert card.patient_id == DEMO_PATIENT_1_ID
    assert card.issued_by == provider_id
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_routine_seed_refuses_to_recreate_missing_existing_clinical_records():
    session = AsyncMock()
    clinical_rows = MagicMock()
    clinical_rows.mappings.return_value.all.return_value = []
    session.execute.side_effect = [MagicMock(), clinical_rows]

    with pytest.raises(RuntimeError, match="missing synthetic clinical records"):
        await seed_clinical_records(session, DEMO_PATIENT_1_ID, "aarav")

    assert session.execute.await_count == 2


@pytest.mark.asyncio
async def test_seed_creates_clinical_records_only_for_a_new_demo_patient():
    session = AsyncMock()
    clinical_rows = MagicMock()
    clinical_rows.mappings.return_value.all.return_value = []
    session.execute.side_effect = [MagicMock(), clinical_rows, MagicMock()]

    await seed_clinical_records(
        session,
        DEMO_PATIENT_1_ID,
        "aarav",
        patient_created=True,
    )

    assert session.execute.await_count == 3
    assert "pg_advisory_xact_lock" in str(session.execute.await_args_list[0].args[0])


@pytest.mark.asyncio
async def test_routine_seed_preserves_existing_clinical_records():
    session = AsyncMock()
    clinical_rows = MagicMock()
    clinical_rows.mappings.return_value.all.return_value = [
        {
            "diagnoses": ["Type 2 Diabetes Mellitus", "Essential Hypertension"],
            "lab_results": ["HbA1c 7.2%", "Blood Pressure 148/92 mmHg"],
            "prescriptions": ["Metformin 500mg OD", "Lisinopril 10mg OD"],
            "clinical_data": None,
        }
    ]
    session.execute.side_effect = [MagicMock(), clinical_rows]

    await seed_clinical_records(session, DEMO_PATIENT_1_ID, "aarav")

    assert session.execute.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rows",
    [
        [
            {
                "diagnoses": ["Altered"],
                "lab_results": ["HbA1c 7.2%", "Blood Pressure 148/92 mmHg"],
                "prescriptions": ["Metformin 500mg OD", "Lisinopril 10mg OD"],
                "clinical_data": None,
            }
        ],
        [
            {
                "diagnoses": ["Type 2 Diabetes Mellitus", "Essential Hypertension"],
                "lab_results": ["HbA1c 7.2%", "Blood Pressure 148/92 mmHg"],
                "prescriptions": ["Metformin 500mg OD", "Lisinopril 10mg OD"],
                "clinical_data": None,
            },
            {
                "diagnoses": ["Type 2 Diabetes Mellitus", "Essential Hypertension"],
                "lab_results": ["HbA1c 7.2%", "Blood Pressure 148/92 mmHg"],
                "prescriptions": ["Metformin 500mg OD", "Lisinopril 10mg OD"],
                "clinical_data": None,
            },
        ],
    ],
)
async def test_routine_seed_refuses_altered_or_duplicate_clinical_fixture(rows):
    session = AsyncMock()
    clinical_rows = MagicMock()
    clinical_rows.mappings.return_value.all.return_value = rows
    session.execute.side_effect = [MagicMock(), clinical_rows]

    with pytest.raises(RuntimeError, match="altered or duplicated"):
        await seed_clinical_records(session, DEMO_PATIENT_1_ID, "aarav")

    assert session.execute.await_count == 2


@pytest.mark.asyncio
async def test_main_reset_revokes_sessions_and_writes_audit(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://demo@127.0.0.1/nexa_qual_demo_test",
    )
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
    seed_order: list[str] = []

    async def record_authoritative_patients(active_session):
        assert active_session is session
        seed_order.append("patients")
        return frozenset()

    async def record_local_patient_identities(
        active_session, *, newly_created_patient_ids
    ):
        assert active_session is session
        assert newly_created_patient_ids == frozenset()
        seed_order.append("patient-auth")

    async def record_clinical_trust(
        active_session, active_hospital_id, active_provider_id, **kwargs
    ):
        assert active_session is session
        assert active_hospital_id == hospital_id
        assert active_provider_id == provider_id
        assert kwargs == {
            "provider_created": False,
            "hospital_created": False,
            "affiliation_created": False,
            "allow_reactivation": False,
        }
        seed_order.append("trust")

    async def record_nfc_card(active_session, patient_id, active_provider_id, **kwargs):
        assert active_session is session
        assert patient_id == DEMO_PATIENT_1_ID
        assert active_provider_id == provider_id
        assert kwargs == {"patient_created": False, "allow_reactivation": False}
        seed_order.append("nfc")

    async def record_clinical_records(active_session, patient_id, name, **kwargs):
        assert active_session is session
        assert kwargs == {"patient_created": False, "allow_reactivation": False}
        seed_order.append(f"clinical:{name}:{patient_id}")

    with (
        patch(
            "scripts.seed_demo_doctor.get_session_factory", return_value=session_factory
        ),
        patch(
            "scripts.seed_demo_doctor.seed_hospital",
            new=AsyncMock(
                return_value=HospitalSeedResult(
                    hospital_id=hospital_id, hospital_created=False
                )
            ),
        ),
        patch(
            "scripts.seed_demo_doctor.seed_provider", new=AsyncMock(return_value=result)
        ),
        patch(
            "scripts.seed_demo_doctor.seed_demo_clinical_trust",
            new=AsyncMock(side_effect=record_clinical_trust),
        ),
        patch(
            "scripts.seed_demo_doctor.seed_authoritative_patients",
            new=AsyncMock(side_effect=record_authoritative_patients),
        ) as seed_patients,
        patch(
            "scripts.seed_demo_doctor.seed_local_demo_patient_auth_identities",
            new=AsyncMock(side_effect=record_local_patient_identities),
        ) as seed_patient_auth,
        patch(
            "scripts.seed_demo_doctor.seed_nfc_card",
            new=AsyncMock(side_effect=record_nfc_card),
        ),
        patch(
            "scripts.seed_demo_doctor.seed_clinical_records",
            new=AsyncMock(side_effect=record_clinical_records),
        ),
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
    seed_patients.assert_awaited_once_with(session)
    seed_patient_auth.assert_awaited_once_with(
        session, newly_created_patient_ids=frozenset()
    )
    assert seed_order == [
        "trust",
        "patients",
        "patient-auth",
        "nfc",
        f"clinical:aarav:{DEMO_PATIENT_1_ID}",
        f"clinical:priya:{DEMO_PATIENT_2_ID}",
    ]
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_main_reset_rolls_back_when_audit_fails(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://demo@127.0.0.1/nexa_qual_demo_test",
    )
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
            new=AsyncMock(
                return_value=HospitalSeedResult(
                    hospital_id=uuid.uuid4(), hospital_created=False
                )
            ),
        ),
        patch(
            "scripts.seed_demo_doctor.seed_provider", new=AsyncMock(return_value=result)
        ),
        patch(
            "scripts.seed_demo_doctor.seed_demo_clinical_trust",
            new=AsyncMock(),
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
