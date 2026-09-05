"""Production-shaped provider/patient routine-access proof.

This suite intentionally uses real PostgreSQL, Redis, route dependencies,
audit persistence, provider login, and ECDSA P-256 verification.  It skips
unless the caller supplies loopback disposable infrastructure.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from urllib.parse import urlsplit
import uuid
from datetime import datetime, timezone

import httpx
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
import pyotp
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import get_async_engine, get_session_factory
from app.core.redis import get_async_redis_client, get_redis_client
from app.core.security import encrypt_mfa_secret
from app.main import app
from app.models.nfc_card_registry import NFCCardRegistry
from app.models.patient import Patient
from app.models.patient_device_keys import PatientDeviceKey
from app.models.patient_records import Vitals
from app.models.provider import ProviderCredential
from app.services.patient_auth_service import issue_patient_access_token
from app.services.provider_auth_service import hash_provider_password
from app.services.signed_approval_verifier import canonical_signed_approval_payload
from tests.helpers.qualification_infra import (
    get_qualification_redis_url,
    postgres_database_url,
    require_disposable_database_name,
    require_loopback_postgres_url,
    require_loopback_redis_url,
    seed_qualification_provider_trust,
)


pytestmark = [pytest.mark.postgres, pytest.mark.redis, pytest.mark.asyncio]

_DB_NAME = "nexa_qual_ci_shared"
_PUBLIC_ID_RE = re.compile(r"^NC-[0-9A-F]{24}$")


@pytest.fixture(autouse=True)
def override_deps():
    """Shadow the global mock fixture: this suite requires production dependencies."""

    app.dependency_overrides.clear()
    try:
        yield
    finally:
        app.dependency_overrides.clear()


def _get_db_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if url:
        require_loopback_postgres_url(url)
        db_name = urlsplit(url).path.lstrip("/")
        require_disposable_database_name(db_name)
        return url
    return postgres_database_url(_DB_NAME)


def _get_redis_url() -> str:
    url = os.getenv("TEST_REDIS_URL")
    if url:
        require_loopback_redis_url(url)
        return url
    return get_qualification_redis_url()


async def _seed_graph(db_url: str) -> dict[str, object]:
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    private_key = ec.generate_private_key(ec.SECP256R1())
    der_public_key = private_key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    hospital_id = uuid.uuid4()
    provider_id = uuid.uuid4()
    password = "E2E-disposable-password-123!"
    totp_secret = pyotp.random_base32()
    suffix = uuid.uuid4().hex
    card_uid = f"E2E-{suffix[:20]}".upper()

    try:
        async with factory() as db:
            await seed_qualification_provider_trust(
                db,
                hospital_id=hospital_id,
                provider_id=provider_id,
                roles=["clinician"],
                issue_session=False,
            )
            cred = (
                await db.execute(
                    select(ProviderCredential).where(
                        ProviderCredential.provider_id == provider_id
                    )
                )
            ).scalar_one()
            cred.password_hash = hash_provider_password(password)
            cred.mfa_enabled = True
            cred.mfa_secret_encrypted = encrypt_mfa_secret(totp_secret)
            login_identifier = cred.login_identifier

            patient = Patient(is_deleted=False)
            db.add(patient)
            await db.flush()

            card = NFCCardRegistry(
                card_uid=card_uid,
                patient_id=patient.patient_uuid,
                issued_by=provider_id,
                status="active",
            )
            device = PatientDeviceKey(
                patient_id=patient.patient_uuid,
                device_public_key=der_public_key,
                device_label="Disposable E2E Device",
                platform="ios",
                key_algorithm="ECDSA-P256",
                status="active",
                enrolled_at=datetime.now(timezone.utc),
            )
            vital = Vitals(
                patient_id=patient.patient_uuid,
                type="BP",
                value="120/80",
                unit="mmHg",
                recorded_at=datetime.now(timezone.utc),
                source="manual",
                risk_level="LOW_RISK",
            )
            db.add_all([card, device, vital])
            await db.commit()
            return {
                "private_key": private_key,
                "patient_id": str(patient.patient_uuid),
                "public_patient_id": patient.public_patient_id,
                "device_id": str(device.id),
                "card_uid": card_uid,
                "provider_id": str(provider_id),
                "hospital_id": str(hospital_id),
                "login_identifier": login_identifier,
                "password": password,
                "totp_secret": totp_secret,
            }
    finally:
        await engine.dispose()


def _provider_headers(token: str, hospital_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Hospital-Id": hospital_id,
        "User-Agent": "Nexa-True-E2E/1.0",
    }


async def _approve(
    client: httpx.AsyncClient,
    *,
    patient_token: str,
    private_key: ec.EllipticCurvePrivateKey,
    request_id: str,
    patient_id: str,
    device_id: str,
) -> None:
    challenge = await client.get(
        f"/api/v2/consent/challenge/{request_id}",
        headers={"Authorization": f"Bearer {patient_token}"},
    )
    assert challenge.status_code == 200, challenge.text
    data = challenge.json()
    signing_bytes = canonical_signed_approval_payload(
        request_id=request_id,
        patient_id=patient_id,
        provider_id=data["provider_id"],
        challenge_nonce=data["challenge_nonce"],
        decision="approved",
        purpose=data["purpose"],
        scope=data["scope"],
        issued_at=data["issued_at"],
        expires_at=data["expires_at"],
        access_duration=data["access_duration"],
        device_id=device_id,
    )
    digest = hashlib.sha256(signing_bytes).digest()
    signature = private_key.sign(digest, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
    response = await client.post(
        "/api/v2/consent/approve-signed",
        headers={"Authorization": f"Bearer {patient_token}"},
        json={
            "request_id": request_id,
            "patient_id": patient_id,
            "decision": "approved",
            "challenge_nonce": data["challenge_nonce"],
            "signature": base64.b64encode(signature).decode("ascii"),
            "device_id": device_id,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "approved"
    assert "consent_token" not in response.json()


async def test_true_patient_provider_routine_access_e2e() -> None:
    db_url = _get_db_url()
    redis_url = _get_redis_url()

    env_overrides = {
        "TEST_DATABASE_URL": db_url,
        "DATABASE_URL": db_url,
        "TEST_REDIS_URL": redis_url,
        "UPSTASH_REDIS_URL": redis_url,
        "PATIENT_JWT_SECRET": os.getenv(
            "PATIENT_JWT_SECRET", "test-secret-at-least-32-chars-long-here!!"
        ),
        "OTP_RATE_LIMIT_HMAC_SECRET": os.getenv(
            "OTP_RATE_LIMIT_HMAC_SECRET", "test-secret-at-least-32-chars-long-here!!"
        ),
        "MFA_ENCRYPTION_KEY": os.getenv(
            "MFA_ENCRYPTION_KEY", Fernet.generate_key().decode("utf-8")
        ),
        "ENVIRONMENT": "test",
        "ENV": "test",
    }
    prev_env = {k: os.environ.get(k) for k in env_overrides}
    os.environ.update(env_overrides)

    get_async_engine.cache_clear()
    get_session_factory.cache_clear()
    get_redis_client.cache_clear()
    get_async_redis_client.cache_clear()
    redis = Redis.from_url(redis_url, decode_responses=True)
    await redis.flushdb()
    graph = await _seed_graph(db_url)
    assert _PUBLIC_ID_RE.fullmatch(str(graph["public_patient_id"]))
    patient_token, _ = issue_patient_access_token(
        str(graph["patient_id"]), "e2e-patient-subject"
    )

    engine = create_async_engine(db_url)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            login = await client.post(
                "/api/v2/auth/login",
                headers={"User-Agent": "Nexa-True-E2E/1.0"},
                json={
                    "login_identifier": graph["login_identifier"],
                    "password": graph["password"],
                    "hospital_id": graph["hospital_id"],
                },
            )
            assert login.status_code == 200, login.text
            mfa_token = login.json()["mfa_token"]

            mfa_verify = await client.post(
                "/api/v2/auth/mfa/verify",
                headers={"User-Agent": "Nexa-True-E2E/1.0"},
                json={
                    "mfa_token": mfa_token,
                    "totp_code": pyotp.TOTP(graph["totp_secret"]).now(),
                    "hospital_id": graph["hospital_id"],
                },
            )
            assert mfa_verify.status_code == 200, mfa_verify.text
            provider_token = mfa_verify.json()["access_token"]
            headers = _provider_headers(provider_token, str(graph["hospital_id"]))

            pre_consent = await client.get(
                f"/api/v2/patient/{graph['patient_id']}/summary", headers=headers
            )
            assert pre_consent.status_code == 403

            nfc = await client.post(
                "/api/v2/nfc/resolve",
                headers=headers,
                json={"card_uid": graph["card_uid"]},
            )
            assert nfc.status_code == 200, nfc.text
            discovery = nfc.json()
            assert set(discovery) == {"discovery_handle", "expires_at"}
            assert not any(
                field in discovery
                for field in (
                    "patient_id",
                    "patient_uuid",
                    "canonical_patient_id",
                    "public_patient_id",
                    "clinical_data",
                )
            )

            request = await client.post(
                "/api/v2/consent/request",
                headers=headers,
                json={
                    "discovery_handle": discovery["discovery_handle"],
                    "purpose": "treatment",
                    "scope": "clinical",
                    "access_duration_seconds": 900,
                },
            )
            assert request.status_code == 201, request.text
            request_data = request.json()
            assert request_data["status"] == "pending"
            request_id = request_data["request_id"]
            assert 110 <= await redis.ttl(f"consent_request:{request_id}") <= 120

            reused = await client.post(
                "/api/v2/consent/request",
                headers=headers,
                json={
                    "discovery_handle": discovery["discovery_handle"],
                    "purpose": "treatment",
                    "scope": "clinical",
                    "access_duration_seconds": 900,
                },
            )
            assert reused.status_code == 403

            await _approve(
                client,
                patient_token=patient_token,
                private_key=graph["private_key"],
                request_id=request_id,
                patient_id=str(graph["patient_id"]),
                device_id=str(graph["device_id"]),
            )

            status_response = await client.get(
                f"/api/v2/consent/status/{request_id}", headers=headers
            )
            assert status_response.status_code == 200
            assert status_response.json()["status"] == "approved"

            claim = await client.post(
                f"/api/v2/consent/{request_id}/claim-access", headers=headers
            )
            assert claim.status_code == 200, claim.text
            claim_data = claim.json()
            assert claim_data["patient_id"] == graph["patient_id"]
            assert claim_data["purpose"] == "treatment"
            assert claim_data["scope"] == "clinical"
            assert claim_data["consent_token"]

            replay = await client.post(
                f"/api/v2/consent/{request_id}/claim-access", headers=headers
            )
            assert replay.status_code in {403, 409}

            summary = await client.get(
                f"/api/v2/patient/{claim_data['patient_id']}/summary",
                headers={**headers, "X-Consent-Token": claim_data["consent_token"]},
            )
            assert summary.status_code == 200, summary.text
            assert summary.json()["patient_id"] == graph["patient_id"]
            assert summary.json()["clinical_summary"]["latest_vitals"]

            full_scope = await client.get(
                f"/api/v2/patient/{claim_data['patient_id']}/structured-record",
                headers={**headers, "X-Consent-Token": claim_data["consent_token"]},
            )
            assert full_scope.status_code == 403

        stored_request = json.loads(await redis.get(f"consent_request:{request_id}"))
        assert stored_request["status"] == "approved"
        assert await redis.get(f"consent_access:claim:{request_id}")
        capability_keys = await redis.keys("consent_access:capability:*")
        assert len(capability_keys) == 1

        async with engine.connect() as conn:
            events = (
                await conn.execute(
                    text(
                        "SELECT action, details::text FROM public.audit_ledger "
                        "WHERE action IN ('NFC_CARD_RESOLVED', 'CONSENT_REQUEST_CREATED', "
                        "'CONSENT_APPROVED_SIGNED', 'CONSENT_ACCESS_CLAIMED', "
                        "'PATIENT_RECORD_READ_SUCCESS')"
                    )
                )
            ).all()
        names = {row[0] for row in events}
        assert {
            "NFC_CARD_RESOLVED",
            "CONSENT_REQUEST_CREATED",
            "CONSENT_APPROVED_SIGNED",
            "CONSENT_ACCESS_CLAIMED",
            "PATIENT_RECORD_READ_SUCCESS",
        } <= names
        audit_text = "\n".join(str(row[1]) for row in events)
        assert str(discovery["discovery_handle"]) not in audit_text
        assert str(claim_data["consent_token"]) not in audit_text
        assert str(graph["card_uid"]) not in audit_text
    finally:
        await engine.dispose()
        await redis.close()
        await get_async_redis_client().close()
        get_async_redis_client.cache_clear()
        get_redis_client().close()
        get_redis_client.cache_clear()
        for k, v in prev_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
