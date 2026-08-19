"""Integration tests for Squad C: Cryptographic Erasure.

Contract:
- Overwrite/delete DEK for specific patient.
- Verify decryption now fails with PatientDataErased.
- Audit trail for destruction.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.v2.patient_routes import _fetch_clinical_shard, get_kms_provider
from app.core.dependencies import (
    get_current_provider,
    get_db_session,
    get_provider_context,
    require_role,
)
from app.main import app
from app.models.provider import AffiliationType
from app.models.provider_context import (
    AffiliationContext,
    HospitalContext,
    ProviderContext,
    ProviderIdentityContext,
)
from app.models.shards import NexaClinical, NexaVault
from app.services.consent_engine import ConsentPurpose, issue_routine
from app.services.crypto_kms import LocalEnvelopeProvider, PatientDataErased


class MockRedis:
    def __init__(self):
        self.data = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        self.data[key] = value

    async def delete(self, key):
        self.data.pop(key, None)

    async def getdel(self, key):
        return self.data.pop(key, None)

    async def rpush(self, key, value):
        self.data.setdefault(key, []).append(value)


def _provider() -> ProviderContext:
    return ProviderContext(
        provider=ProviderIdentityContext(
            provider_id=uuid.uuid4(), display_name="Dr. Erase", contact_email="e@ex.com"
        ),
        hospital=HospitalContext(
            hospital_id=uuid.uuid4(), facility_code="H", display_name="H"
        ),
        affiliation=AffiliationContext(
            affiliation_id=uuid.uuid4(),
            affiliation_type=AffiliationType.PERMANENT,
            is_primary=True,
            roles=["clinician", "admin"],
        ),
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cryptographic_erasure_workflow(monkeypatch):
    """DEFECT 5 (was Squad C Day 4 xfail): real end-to-end erasure through
    the actual HTTP routes -- issue consent, encrypt a field, read it back
    successfully, erase via POST /erase, then prove GET /record now fails
    with the patient-data-erased contract instead of silently succeeding
    or 500ing with no signal."""
    monkeypatch.setenv("KEK_ROOT_SECRET", "test-root-secret-long-enough-32-chars-!!")

    patient_id = str(uuid.uuid4())
    provider = _provider()
    hospital_id = str(provider.hospital_id)
    kms = LocalEnvelopeProvider()
    redis = MockRedis()

    db = MagicMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.rollback = AsyncMock()
    db.added_rows = []
    db.add = MagicMock(side_effect=lambda obj: db.added_rows.append(obj))
    no_destroyed = MagicMock()
    no_destroyed.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=no_destroyed)

    await kms.generate_dek(patient_id, db)
    dek_row = db.added_rows[0]

    async def db_execute_default(stmt):
        stmt_str = str(stmt)
        res = MagicMock()
        if "patient_dek_store" in stmt_str:
            res.scalar_one_or_none.return_value = dek_row
            res.scalars().all.return_value = [dek_row]
            return res
        if "patient_erasure_tombstones" in stmt_str:
            res.scalar_one_or_none.return_value = None
            return res
        return res

    db.execute = AsyncMock(side_effect=db_execute_default)
    encrypted_name = await kms.encrypt_field(patient_id, "patient_name", "Jane Doe", db)
    vault_row = NexaVault(
        masked_internal_id=patient_id, patient_name=encrypted_name.serialize()
    )

    async def db_execute_with_vault(stmt):
        stmt_str = str(stmt)
        res = MagicMock()
        if "patient_dek_store" in stmt_str:
            res.scalar_one_or_none.return_value = dek_row
            res.scalars().all.return_value = [dek_row]
            return res
        if "nexa_vault" in stmt_str:
            res.scalars().first.return_value = vault_row
            return res
        if "nexa_clinical" in stmt_str:
            res.scalars().first.return_value = None
            return res
        if "patient_erasure_tombstones" in stmt_str:
            res.scalar_one_or_none.return_value = None
            return res
        return res

    db.execute = AsyncMock(side_effect=db_execute_with_vault)

    with (
        patch(
            "app.services.consent_engine.get_consent_redis_client", return_value=redis
        ),
        patch("app.services.consent_engine.append_audit_log_or_503", AsyncMock()),
        patch("app.services.consent_engine.append_audit_log", AsyncMock()),
    ):
        token = await issue_routine(
            patient_id=patient_id,
            clinician_id=provider.actor_uid,
            purpose=ConsentPurpose.TREATMENT,
            scope=["pii.patient_name"],
            db=db,
            hospital_id=hospital_id,
        )

    app.dependency_overrides[get_db_session] = lambda: db
    app.dependency_overrides[get_current_provider] = lambda: provider
    app.dependency_overrides[get_provider_context] = lambda: provider
    app.dependency_overrides[require_role("admin")] = lambda: provider
    app.dependency_overrides[get_kms_provider] = lambda: kms

    client = TestClient(app)

    try:
        with (
            patch(
                "app.services.consent_engine.get_consent_redis_client",
                return_value=redis,
            ),
            patch(
                "app.api.v2.patient_routes.get_consent_redis_client", return_value=redis
            ),
            patch(
                "app.services.consent_gated_crypto.append_audit_log_or_503", AsyncMock()
            ),
            patch("app.services.consent_gated_crypto.append_audit_log", AsyncMock()),
        ):
            # Read succeeds BEFORE erasure -- proves the setup is real, not
            # trivially erased-by-default.
            pre_erasure = client.get(
                f"/api/v2/patient/{patient_id}/record",
                headers={"X-Consent-Token": token, "X-Consent-Purpose": "TREATMENT"},
            )
            assert pre_erasure.status_code == 200, pre_erasure.text
            assert pre_erasure.json()["pii"]["patient_name"] == "Jane Doe"

            # 1. Erase.
            with (
                patch("app.api.v2.patient_routes.append_audit_log_or_503", AsyncMock()),
                patch(
                    "app.observability.audit_ledger.append_audit_log",
                    AsyncMock(return_value=True),
                ),
            ):
                erase_resp = client.post(
                    f"/api/v2/patient/{patient_id}/erase",
                    json={
                        "confirmation": f"ERASE-{patient_id}",
                        "reason": "Request by patient",
                    },
                )
            assert erase_resp.status_code == 200, erase_resp.text
            assert (
                db.added_rows[-1] is not dek_row or dek_row not in db.added_rows
            )  # sanity: destroy ran

            # A fresh, un-expired routine token would still validate against
            # Redis, but the DEK backing it is now gone -- issue a second
            # token post-erasure to isolate "data erased" from "token expired".
            token2 = await issue_routine(
                patient_id=patient_id,
                clinician_id=provider.actor_uid,
                purpose=ConsentPurpose.TREATMENT,
                scope=["pii.patient_name"],
                db=db,
                hospital_id=hospital_id,
            )

            # 2. Attempt read after erasure.
            read_resp = client.get(
                f"/api/v2/patient/{patient_id}/record",
                headers={"X-Consent-Token": token2, "X-Consent-Purpose": "TREATMENT"},
            )

        assert read_resp.status_code == 410, read_resp.text
        assert "erased" in read_resp.text.lower()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_clinical_data_persists_after_pii_erasure(monkeypatch):
    """Test: clinical shard rows remain readable after PII DEK destruction."""
    monkeypatch.setenv("KEK_ROOT_SECRET", "test-root-secret-long-enough-32-chars-!!")

    patient_id = str(uuid.uuid4())
    db = MagicMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    no_destroyed = MagicMock()
    no_destroyed.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=no_destroyed)

    kms = LocalEnvelopeProvider()
    await kms.generate_dek(patient_id, db)
    dek_row = db.add.call_args.args[0]

    active_dek_result = MagicMock()
    active_dek_result.scalar_one_or_none.return_value = dek_row
    db.execute.return_value = active_dek_result
    encrypted_name = await kms.encrypt_field(patient_id, "patient_name", "Jane Doe", db)

    async def execute_for_destroy(stmt):
        result = MagicMock()
        if "patient_dek_store" in str(stmt):
            result.scalars().all.return_value = [dek_row]
        return result

    db.execute.side_effect = execute_for_destroy
    with patch("app.observability.audit_ledger.append_audit_log", AsyncMock()):
        await kms.destroy_dek(patient_id, db)

    clinical_row = NexaClinical(
        masked_internal_id=patient_id,
        diagnoses=["hypertension"],
        lab_results=["HbA1c 6.1%"],
        prescriptions=["metformin"],
        clinical_data={"blood_pressure": "120/80"},
    )

    async def execute_after_erasure(stmt):
        result = MagicMock()
        stmt_text = str(stmt)
        if "nexa_clinical" in stmt_text:
            result.scalars().first.return_value = clinical_row
        elif "patient_dek_store" in stmt_text:
            result.scalar_one_or_none.return_value = dek_row
        return result

    db.execute.side_effect = execute_after_erasure

    clinical_payload = await _fetch_clinical_shard(patient_id, db)
    assert clinical_payload["diagnoses"] == ["hypertension"]
    assert clinical_payload["lab_results"] == ["HbA1c 6.1%"]
    assert clinical_payload["prescriptions"] == ["metformin"]
    assert clinical_payload["blood_pressure"] == "120/80"

    with pytest.raises(PatientDataErased):
        await kms.decrypt_field(patient_id, "patient_name", encrypted_name, db)
