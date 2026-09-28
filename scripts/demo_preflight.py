#!/usr/bin/env python3
"""Read-only deterministic development preflight for the Nexa Care demo stack."""

from __future__ import annotations

import asyncio
import inspect as stdlib_inspect
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from dotenv import load_dotenv
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import create_async_engine
import redis.asyncio as redis_async

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env", override=False)

from app.core.config import get_database_config, get_redis_config  # noqa: E402
from app.models.nfc_card_registry import NFCCardRegistry, NFCCardStatus  # noqa: E402
from app.models.patient import Patient  # noqa: E402
from app.models.provider import HospitalRegistry, ProviderIdentity  # noqa: E402
from app.security.clinical_policy import CLINICAL_CONTACT_ASSURANCE_POLICY  # noqa: E402
from app.security.erasure_registry import check_erasure_registry  # noqa: E402
from app.security.provider_capabilities import ClinicalCapability  # noqa: E402
from app.services.clinical_eligibility import (  # noqa: E402
    ClinicalAuthenticationMethod,
    ClinicalEligibilityService,
    InteractiveClinicalAuthentication,
)
from scripts.demo_environment import require_demo_environment  # noqa: E402
from scripts.seed_demo_doctor import (  # noqa: E402
    DEMO_HOSPITAL_CODE,
    DEMO_NFC_UID,
    DEMO_PATIENT_1_ID,
    DEMO_PATIENT_2_ID,
    DEMO_PROVIDER_EMAIL,
    demo_public_patient_id,
)

_REQUIRED_PROVIDER_TABLES = {
    "provider_identity",
    "provider_credential",
    "professional_verification",
    "hospital_registry",
    "facility_verification",
    "provider_hospital_affiliation",
}
_REQUIRED_PROVIDER_COLUMNS = {
    "provider_identity": {"email_verified_at", "phone_verified_at", "status", "is_active"},
    "provider_credential": {"mfa_enabled", "mfa_secret_encrypted", "is_active"},
    "provider_hospital_affiliation": {"trust_status", "valid_from", "valid_until", "roles"},
}


def _tool(name: str) -> bool:
    return shutil.which(name) is not None


async def _close_redis_client(client: object) -> None:
    close = getattr(client, "aclose", None)
    if close is None:
        close = getattr(client, "close", None)
    if close is None:
        return

    result = close()
    if stdlib_inspect.isawaitable(result):
        await result


async def _database_revisions(database_url: str) -> tuple[str | None, str]:
    alembic_cfg = Config(str(ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(alembic_cfg)
    head = script.get_current_head()
    if not head:
        raise RuntimeError("Alembic repository has no head")

    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            current = await connection.run_sync(
                lambda sync_conn: MigrationContext.configure(sync_conn).get_current_revision()
            )
    finally:
        await engine.dispose()
    return current, head


async def _schema_ready(database_url: str) -> tuple[bool, list[str]]:
    engine = create_async_engine(database_url)
    problems: list[str] = []
    try:
        async with engine.connect() as connection:
            def inspect_schema(sync_conn):
                inspector = inspect(sync_conn)
                existing_tables = set(inspector.get_table_names())
                for table in sorted(_REQUIRED_PROVIDER_TABLES):
                    if table not in existing_tables:
                        problems.append(f"missing_table:{table}")
                for table, required in _REQUIRED_PROVIDER_COLUMNS.items():
                    if table not in existing_tables:
                        continue
                    columns = {row["name"] for row in inspector.get_columns(table)}
                    for column in sorted(required - columns):
                        problems.append(f"missing_column:{table}.{column}")
            await connection.run_sync(inspect_schema)
    finally:
        await engine.dispose()
    return not problems, problems


async def _demo_state(database_url: str) -> tuple[bool, str | None]:
    engine = create_async_engine(database_url)
    try:
        from sqlalchemy.ext.asyncio import async_sessionmaker

        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            provider = await db.scalar(
                select(ProviderIdentity).where(
                    ProviderIdentity.contact_email == DEMO_PROVIDER_EMAIL
                )
            )
            if provider is None:
                return False, "DEMO_PROVIDER_MISSING"

            hospital_id = await db.scalar(
                select(HospitalRegistry.id).where(
                    HospitalRegistry.facility_code == DEMO_HOSPITAL_CODE
                )
            )
            if hospital_id is None:
                return False, "DEMO_HOSPITAL_MISSING"

            now = datetime.now(timezone.utc)
            authentication = InteractiveClinicalAuthentication(
                provider_id=provider.id,
                hospital_id=hospital_id,
                method=ClinicalAuthenticationMethod.PROVIDER_SESSION,
                session_authenticated=True,
                mfa_verified_at=now,
            )
            eligibility_service = ClinicalEligibilityService(
                contact_assurance_policy=CLINICAL_CONTACT_ASSURANCE_POLICY
            )
            required_capabilities = (
                ClinicalCapability.PATIENT_DISCOVER,
                ClinicalCapability.CONSENT_REQUEST,
                ClinicalCapability.RECORD_READ,
                ClinicalCapability.DOCUMENTS_UPLOAD,
                ClinicalCapability.DOCUMENTS_PROCESS,
                ClinicalCapability.DOCUMENTS_REVIEW,
                ClinicalCapability.DOCUMENTS_COMMIT,
                ClinicalCapability.EMERGENCY_ATTEMPT,
            )
            for capability in required_capabilities:
                eligibility = await eligibility_service.evaluate_interactive(
                    db,
                    provider,
                    authentication,
                    capability,
                    now=now,
                )
                if not eligibility.allowed:
                    denial = (
                        eligibility.denial_code.value
                        if eligibility.denial_code is not None
                        else "CLINICAL_ELIGIBILITY_DENIED"
                    )
                    return False, f"{capability.value}:{denial}"

            for patient_id in (DEMO_PATIENT_1_ID, DEMO_PATIENT_2_ID):
                patient = await db.get(Patient, patient_id)
                if (
                    patient is None
                    or patient.is_deleted
                    or patient.public_patient_id != demo_public_patient_id(patient_id)
                ):
                    return False, "DEMO_PATIENT_DISCOVERY_NOT_READY"
                try:
                    await check_erasure_registry(str(patient_id), db)
                except Exception:
                    return False, "DEMO_PATIENT_ERASURE_BLOCKED_OR_UNAVAILABLE"

            card = await db.scalar(
                select(NFCCardRegistry).where(NFCCardRegistry.card_uid == DEMO_NFC_UID)
            )
            if (
                card is None
                or card.patient_id != DEMO_PATIENT_1_ID
                or card.status != NFCCardStatus.ACTIVE.value
            ):
                return False, "DEMO_NFC_NOT_READY"
            return True, None
    finally:
        await engine.dispose()


async def run_preflight() -> bool:
    environment = require_demo_environment("demo_preflight")
    print("NEXA CARE DEVELOPMENT PREFLIGHT")
    print(f"database_environment_classification={environment}")
    all_go = True

    database_url = get_database_config().url
    host = urlsplit(database_url).hostname or "[not-configured]"
    print(f"database_host={host}")

    try:
        current, head = await _database_revisions(database_url)
        print(f"alembic_current={current or '[none]'}")
        print(f"alembic_head={head}")
        if current != head:
            print("DATABASE_SCHEMA_OUTDATED")
            print(f"current={current or '[none]'}")
            print(f"required={head}")
            print("recommended_command=python -m alembic upgrade head")
            all_go = False
    except Exception as exc:
        print(f"database_revision_check=unavailable error_type={type(exc).__name__}")
        all_go = False

    try:
        schema_ok, problems = await _schema_ready(database_url)
        print(f"provider_trust_schema={'ready' if schema_ok else 'not_ready'}")
        for problem in problems:
            print(f"schema_problem={problem}")
        all_go = schema_ok and all_go
    except Exception as exc:
        print(f"provider_trust_schema=unavailable error_type={type(exc).__name__}")
        all_go = False

    try:
        redis_cfg = get_redis_config()
        client = redis_async.from_url(redis_cfg.url)
        try:
            await client.ping()
            print("redis=reachable")
        finally:
            await _close_redis_client(client)
    except Exception as exc:
        print(f"redis=unavailable error_type={type(exc).__name__}")
        all_go = False

    try:
        ready, denial = await _demo_state(database_url)
        print(f"ready_for_clinical_access={str(ready).lower()}")
        if denial:
            print(f"clinical_denial_code={denial}")
        all_go = ready and all_go
    except Exception as exc:
        print(f"ready_for_clinical_access=false error_type={type(exc).__name__}")
        all_go = False

    expo_dir = ROOT / "nexa-client" / "apps" / "expo"
    local_firebase = expo_dir / "google-services.development.local.json"
    configured = os.getenv("GOOGLE_SERVICES_FILE", "").strip()
    configured_path = (expo_dir / configured).resolve() if configured else None
    firebase_present = local_firebase.exists() or bool(
        configured_path and configured_path.exists()
    )
    print(f"firebase_development_config={'present' if firebase_present else 'absent_optional'}")

    tooling = {
        "java": _tool("java"),
        "adb": _tool("adb"),
        "node": _tool("node"),
        "corepack": _tool("corepack") or _tool("corepack.cmd"),
    }
    print("android_tooling=" + ",".join(f"{k}:{'ok' if v else 'missing'}" for k, v in tooling.items()))

    patient_a_public_id = demo_public_patient_id(DEMO_PATIENT_1_ID)
    patient_b_public_id = demo_public_patient_id(DEMO_PATIENT_2_ID)
    print("DEMO_PATIENT_A")
    print(f"public_id={patient_a_public_id}")
    print(f"nfc_uid={DEMO_NFC_UID}")
    print(f"qr_payload=nexa://patient-discovery/v1/{patient_a_public_id}")
    print("qr_supported=true")
    print("DEMO_PATIENT_B")
    print(f"public_id={patient_b_public_id}")
    print(f"qr_payload=nexa://patient-discovery/v1/{patient_b_public_id}")
    print("qr_supported=true")

    print(f"preflight={'GO' if all_go else 'NO-GO'}")
    return all_go


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(run_preflight()) else 1)
