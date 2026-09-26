from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.provider import (
    AffiliationTrustStatus,
    FacilityVerification,
    FacilityVerificationStatus,
    HospitalRegistry,
    ProfessionalVerification,
    ProfessionalVerificationStatus,
    ProviderCredential,
    ProviderHospitalAffiliation,
    ProviderIdentity,
    ProviderTrustVerificationEvidence,
)
from app.services.prescribing_eligibility_reader import (
    PrescribingEligibilityDenied,
    assert_current_prescribing_eligibility,
)
from scripts.seed_demo_doctor import (
    DEMO_HOSPITAL_CODE,
    DEMO_PROVIDER_EMAIL,
    DEMO_REVIEWER_EMAIL,
    ProviderSeedResult,
    main,
    parse_args,
    require_demo_provider_mfa_secret,
    require_demo_provider_password,
    seed_provider,
    seed_provider_trust,
)


STRONG_PASSWORD = "Alpha-Only-Strong-Password-42!"
DEMO_MFA_SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"


@pytest.fixture(autouse=True)
def _demo_mfa_environment(monkeypatch):
    monkeypatch.setenv("DEMO_PROVIDER_MFA_SECRET", DEMO_MFA_SECRET)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setattr(
        "scripts.seed_demo_doctor.encrypt_mfa_secret",
        lambda secret: "encrypted-demo-secret",
    )
    monkeypatch.setattr(
        "scripts.seed_demo_doctor.decrypt_mfa_secret",
        lambda _ciphertext: DEMO_MFA_SECRET,
    )


def provider_row(*, active: bool = True) -> ProviderIdentity:
    provider = ProviderIdentity(
        display_name="Dr. Meera Joshi",
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
        mfa_enabled=True,
        mfa_secret_encrypted="encrypted-demo-secret",
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
        valid_from=datetime.now(timezone.utc) - timedelta(days=1),
        valid_until=datetime.now(timezone.utc) + timedelta(days=365),
        is_active=True,
        trust_status=AffiliationTrustStatus.ACTIVE.value,
    )
    affiliation.id = uuid.uuid4()
    return affiliation


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


def test_demo_mfa_secret_is_required_and_never_generated_by_the_seeder(monkeypatch):
    monkeypatch.delenv("DEMO_PROVIDER_MFA_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="DEMO_PROVIDER_MFA_SECRET"):
        require_demo_provider_mfa_secret()


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


def test_no_seed_output_statement_contains_password_or_hash():
    source = open("scripts/seed_demo_doctor.py", encoding="utf-8").read()
    print_lines = [line for line in source.splitlines() if "print(" in line]
    assert not any("DEMO_PROVIDER_PASSWORD" in line for line in print_lines)
    assert not any("password_hash" in line for line in print_lines)


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
        patch("scripts.seed_demo_doctor.seed_provider_trust", new=AsyncMock()),
        patch(
            "scripts.seed_demo_doctor.seed_patient_identity",
            new=AsyncMock(
                side_effect=[
                    SimpleNamespace(patient_uuid=uuid.uuid4()),
                    SimpleNamespace(patient_uuid=uuid.uuid4()),
                ]
            ),
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


# ── Legacy Trust Repair Tests ───────────────────────────────────────────────


def make_demo_trust_entities():
    provider_id = uuid.uuid4()
    hospital_id = uuid.uuid4()
    reviewer_id = uuid.uuid4()

    provider = ProviderIdentity(
        display_name="Dr. Meera Joshi",
        contact_email=DEMO_PROVIDER_EMAIL,
        medical_registration_number="MMC-2019-45231",
        status="active",
        is_active=True,
    )
    provider.id = provider_id

    hospital = HospitalRegistry(
        facility_code=DEMO_HOSPITAL_CODE,
        legal_name="Nexa Care Demo Hospital Pvt. Ltd.",
        display_name="Nexa Demo Hospital",
        is_active=True,
    )
    hospital.id = hospital_id

    reviewer = ProviderIdentity(
        display_name="Nexa Demo Trust Reviewer",
        contact_email=DEMO_REVIEWER_EMAIL,
        status="active",
        is_active=True,
    )
    reviewer.id = reviewer_id

    return provider, hospital, reviewer


def fake_trust_session(
    *,
    provider: ProviderIdentity,
    hospital: HospitalRegistry,
    reviewer: ProviderIdentity,
    professional: ProfessionalVerification | None = None,
    facility: FacilityVerification | None = None,
    existing_evidence: dict[str, ProviderTrustVerificationEvidence] | None = None,
):
    session = AsyncMock()
    added_rows: list[object] = []

    def assign_id(row: object) -> None:
        added_rows.append(row)
        if getattr(row, "id", None) is None:
            setattr(row, "id", uuid.uuid4())

    session.add = MagicMock(side_effect=assign_id)

    async def fake_get(model: type, entity_id: uuid.UUID) -> object | None:
        if model is ProviderIdentity and entity_id == provider.id:
            return provider
        if model is HospitalRegistry and entity_id == hospital.id:
            return hospital
        return None

    session.get = AsyncMock(side_effect=fake_get)

    evidence_map = dict(existing_evidence or {})

    async def fake_scalar(stmt: object) -> object | None:
        col_descs = getattr(stmt, "column_descriptions", None)
        entity = col_descs[0].get("entity") if col_descs else None
        if entity is ProviderIdentity:
            return reviewer
        if entity is ProfessionalVerification:
            return professional
        if entity is FacilityVerification:
            return facility
        if entity is ProviderTrustVerificationEvidence:
            source_id = getattr(stmt.whereclause.right, "value", None)
            if source_id in evidence_map:
                return evidence_map[source_id]
            for r in added_rows:
                if (
                    isinstance(r, ProviderTrustVerificationEvidence)
                    and r.source_id == source_id
                ):
                    return r
            return None
        return None

    session.scalar = AsyncMock(side_effect=fake_scalar)

    async def fake_execute(stmt: object) -> object:
        val = await fake_scalar(stmt)
        result = MagicMock()
        result.scalar_one_or_none.return_value = val
        return result

    session.execute = AsyncMock(side_effect=fake_execute)
    return session, added_rows


def test_parse_args_trust_repair_flags():
    with pytest.raises(SystemExit):
        parse_args(["--repair-legacy-demo-trust"])

    with pytest.raises(SystemExit):
        parse_args(["--confirm-demo-trust-repair"])

    args = parse_args(["--repair-legacy-demo-trust", "--confirm-demo-trust-repair"])
    assert args.repair_legacy_demo_trust is True
    assert args.confirm_demo_trust_repair is True

    default_args = parse_args([])
    assert default_args.repair_legacy_demo_trust is False
    assert default_args.confirm_demo_trust_repair is False


@pytest.mark.asyncio
async def test_trust_repair_legacy_not_submitted_and_draft(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    provider, hospital, reviewer = make_demo_trust_entities()
    professional = ProfessionalVerification(
        provider_id=provider.id,
        status=ProfessionalVerificationStatus.NOT_SUBMITTED.value,
        version=1,
    )
    facility = FacilityVerification(
        facility_id=hospital.id,
        status=FacilityVerificationStatus.DRAFT.value,
        version=1,
    )
    session, added = fake_trust_session(
        provider=provider,
        hospital=hospital,
        reviewer=reviewer,
        professional=professional,
        facility=facility,
    )

    await seed_provider_trust(
        session, provider.id, hospital.id, repair_legacy_trust=True
    )

    assert professional.status == ProfessionalVerificationStatus.VERIFIED.value
    assert professional.reviewer_id == str(reviewer.id)
    assert professional.decision_reason_code == "SYNTHETIC_DEMO_VERIFIED"
    assert professional.server_provenance_evidence_id is not None
    assert facility.status == FacilityVerificationStatus.VERIFIED.value
    assert facility.reviewer_id == str(reviewer.id)
    assert facility.decision_reason_code == "SYNTHETIC_DEMO_VERIFIED"
    assert facility.server_provenance_evidence_id is not None


@pytest.mark.asyncio
async def test_trust_repair_denied_in_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("ENV", "production")
    provider, hospital, reviewer = make_demo_trust_entities()
    session, _ = fake_trust_session(
        provider=provider, hospital=hospital, reviewer=reviewer
    )

    with pytest.raises(RuntimeError, match="Refusing to run seed_demo_doctor_trust_repair"):
        await seed_provider_trust(
            session, provider.id, hospital.id, repair_legacy_trust=True
        )


@pytest.mark.asyncio
async def test_trust_repair_fails_closed_without_flag(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    provider, hospital, reviewer = make_demo_trust_entities()
    professional = ProfessionalVerification(
        provider_id=provider.id,
        status=ProfessionalVerificationStatus.NOT_SUBMITTED.value,
        version=1,
    )
    facility = FacilityVerification(
        facility_id=hospital.id,
        status=FacilityVerificationStatus.DRAFT.value,
        version=1,
    )
    session, _ = fake_trust_session(
        provider=provider,
        hospital=hospital,
        reviewer=reviewer,
        professional=professional,
        facility=facility,
    )

    with pytest.raises(RuntimeError, match="Demo professional verification is not VERIFIED"):
        await seed_provider_trust(
            session, provider.id, hospital.id, repair_legacy_trust=False
        )


@pytest.mark.asyncio
async def test_trust_repair_fails_closed_on_rejected_or_suspended_professional(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    provider, hospital, reviewer = make_demo_trust_entities()
    for bad_status in ("REJECTED", "SUSPENDED", "REVOKED", "EXPIRED", "RECHECK_DUE"):
        professional = ProfessionalVerification(
            provider_id=provider.id,
            status=bad_status,
            version=1,
        )
        session, _ = fake_trust_session(
            provider=provider,
            hospital=hospital,
            reviewer=reviewer,
            professional=professional,
        )
        with pytest.raises(RuntimeError, match="is non-initial; refusing trust repair"):
            await seed_provider_trust(
                session, provider.id, hospital.id, repair_legacy_trust=True
            )


@pytest.mark.asyncio
async def test_trust_repair_fails_closed_on_rejected_or_closed_facility(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    provider, hospital, reviewer = make_demo_trust_entities()
    professional = ProfessionalVerification(
        provider_id=provider.id,
        status=ProfessionalVerificationStatus.VERIFIED.value,
        version=1,
    )
    for bad_status in ("REJECTED", "CLOSED", "SUSPENDED", "REVOKED", "RECHECK_DUE"):
        facility = FacilityVerification(
            facility_id=hospital.id,
            status=bad_status,
            version=1,
        )
        session, _ = fake_trust_session(
            provider=provider,
            hospital=hospital,
            reviewer=reviewer,
            professional=professional,
            facility=facility,
        )
        with pytest.raises(RuntimeError, match="is non-initial; refusing trust repair"):
            await seed_provider_trust(
                session, provider.id, hospital.id, repair_legacy_trust=True
            )


@pytest.mark.asyncio
async def test_trust_existing_verified_rows_are_idempotent(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    provider, hospital, reviewer = make_demo_trust_entities()
    professional = ProfessionalVerification(
        provider_id=provider.id,
        status=ProfessionalVerificationStatus.VERIFIED.value,
        version=1,
    )
    facility = FacilityVerification(
        facility_id=hospital.id,
        status=FacilityVerificationStatus.VERIFIED.value,
        version=1,
    )
    session, _ = fake_trust_session(
        provider=provider,
        hospital=hospital,
        reviewer=reviewer,
        professional=professional,
        facility=facility,
    )

    await seed_provider_trust(
        session, provider.id, hospital.id, repair_legacy_trust=False
    )
    assert professional.status == ProfessionalVerificationStatus.VERIFIED.value
    assert facility.status == FacilityVerificationStatus.VERIFIED.value


@pytest.mark.asyncio
async def test_trust_repair_second_run_no_duplicate_evidence(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    provider, hospital, reviewer = make_demo_trust_entities()
    existing_ev1 = ProviderTrustVerificationEvidence(
        source_id="NEXA_DEMO_PROFESSIONAL_EVIDENCE_V1",
        origin="MANUAL_REVIEWER_ATTESTATION",
        observed_at=datetime.now(timezone.utc),
        lookup_purpose="MANUAL_REVIEW",
        outcome="CONFIRMED_ACTIVE",
        source_record_reference="NEXA_DEMO_PROFESSIONAL_EVIDENCE_V1",
        observed_valid_from=datetime.now(timezone.utc) - timedelta(days=30),
        observed_valid_until=datetime.now(timezone.utc) + timedelta(days=365),
        identity_binding_result="MATCHED",
        binding_method="SYNTHETIC_DEMO_BINDING",
        response_digest="abc",
        observed_resource_version=1,
    )
    existing_ev1.id = uuid.uuid4()
    existing_ev2 = ProviderTrustVerificationEvidence(
        source_id="NEXA_DEMO_FACILITY_EVIDENCE_V1",
        origin="MANUAL_REVIEWER_ATTESTATION",
        observed_at=datetime.now(timezone.utc),
        lookup_purpose="MANUAL_REVIEW",
        outcome="CONFIRMED_ACTIVE",
        source_record_reference="NEXA_DEMO_FACILITY_EVIDENCE_V1",
        observed_valid_from=datetime.now(timezone.utc) - timedelta(days=30),
        observed_valid_until=datetime.now(timezone.utc) + timedelta(days=365),
        identity_binding_result="NOT_EVALUATED",
        binding_method="SYNTHETIC_DEMO_BINDING",
        response_digest="def",
        observed_resource_version=1,
    )
    existing_ev2.id = uuid.uuid4()

    professional = ProfessionalVerification(
        provider_id=provider.id,
        status=ProfessionalVerificationStatus.VERIFIED.value,
        server_provenance_evidence_id=existing_ev1.id,
        version=1,
    )
    facility = FacilityVerification(
        facility_id=hospital.id,
        status=FacilityVerificationStatus.VERIFIED.value,
        server_provenance_evidence_id=existing_ev2.id,
        version=1,
    )

    session, added = fake_trust_session(
        provider=provider,
        hospital=hospital,
        reviewer=reviewer,
        professional=professional,
        facility=facility,
        existing_evidence={
            "NEXA_DEMO_PROFESSIONAL_EVIDENCE_V1": existing_ev1,
            "NEXA_DEMO_FACILITY_EVIDENCE_V1": existing_ev2,
        },
    )

    await seed_provider_trust(
        session, provider.id, hospital.id, repair_legacy_trust=True
    )
    assert not any(isinstance(r, ProviderTrustVerificationEvidence) for r in added)


@pytest.mark.asyncio
async def test_trust_reviewer_cannot_be_same_as_provider(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    provider, hospital, _ = make_demo_trust_entities()
    session, _ = fake_trust_session(
        provider=provider,
        hospital=hospital,
        reviewer=provider,
    )

    with pytest.raises(RuntimeError, match="Demo clinical provider cannot self-review trust evidence"):
        await seed_provider_trust(
            session, provider.id, hospital.id, repair_legacy_trust=True
        )


@pytest.mark.asyncio
async def test_prescribing_authority_still_not_granted(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    provider, hospital, reviewer = make_demo_trust_entities()
    professional = ProfessionalVerification(
        provider_id=provider.id,
        status=ProfessionalVerificationStatus.NOT_SUBMITTED.value,
        version=1,
    )
    facility = FacilityVerification(
        facility_id=hospital.id,
        status=FacilityVerificationStatus.DRAFT.value,
        version=1,
    )
    session, _ = fake_trust_session(
        provider=provider,
        hospital=hospital,
        reviewer=reviewer,
        professional=professional,
        facility=facility,
    )

    await seed_provider_trust(
        session, provider.id, hospital.id, repair_legacy_trust=True
    )
    assert professional.status == ProfessionalVerificationStatus.VERIFIED.value

    with pytest.raises(
        PrescribingEligibilityDenied, match="PRESCRIBING_ELIGIBILITY_REQUIRED"
    ):
        await assert_current_prescribing_eligibility(session, provider_id=provider.id)
