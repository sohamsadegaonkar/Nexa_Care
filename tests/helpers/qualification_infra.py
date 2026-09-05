"""Centralized infrastructure helpers for PostgreSQL and Redis qualification tests.

Enforces:
1. Loopback-only host restriction (127.0.0.1 or localhost) to prevent accidental
   connection to staging, shared, or production databases/caches.
2. Disposable database name restriction (must start with 'nexa_qual_') before
   executing any destructive operations (DROP / CREATE DATABASE).
3. Dynamic admin URL derivation inheriting scheme, user, password, host, and port
   while safely targeting the administrative database ('postgres').
4. Loopback-only Redis connection validation.

TEST-ONLY MODULE: Never import this module from app/ or production services.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost"})
_DISPOSABLE_PREFIX = "nexa_qual_"
_DEFAULT_LOCAL_PG_PORT = 55439
_DEFAULT_LOCAL_REDIS_PORT = 6389


def normalize_async_postgres_url(url: str) -> str:
    """Ensure the URL uses the asyncpg driver."""
    if not url:
        raise ValueError("Database URL cannot be empty")
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    raise ValueError(f"Unrecognized PostgreSQL URL scheme in: {url}")


def normalize_sync_postgres_url(url: str) -> str:
    """Ensure the URL uses the standard synchronous postgresql driver (for Alembic)."""
    if not url:
        raise ValueError("Database URL cannot be empty")
    if url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql+asyncpg://", "postgresql://", 1)
    if url.startswith("postgresql://"):
        return url
    raise ValueError(f"Unrecognized PostgreSQL URL scheme in: {url}")


def require_loopback_postgres_url(url: str) -> None:
    """Validate that the PostgreSQL URL targets a loopback interface."""
    normalized = normalize_sync_postgres_url(url)
    parts = urlsplit(normalized)
    hostname = (parts.hostname or "").lower()
    if hostname not in _LOOPBACK_HOSTS:
        raise ValueError(
            f"PostgreSQL qualification URL must target a loopback host ({_LOOPBACK_HOSTS}), "
            f"got host: '{hostname}' in URL: '{url}'"
        )


def require_disposable_database_name(db_name: str) -> None:
    """Validate that the target database name is strictly a disposable qualification DB."""
    if not db_name or not isinstance(db_name, str):
        raise ValueError("Database name must be a non-empty string")
    name = db_name.strip()
    if not name.startswith(_DISPOSABLE_PREFIX):
        raise ValueError(
            f"Destructive qualification operations are strictly restricted to databases "
            f"starting with '{_DISPOSABLE_PREFIX}', got: '{name}'"
        )
    # Strictly disallow system or default database names even if prefixed creatively
    forbidden = {"postgres", "template0", "template1", "nexa", "nexa_ci"}
    if name.lower() in forbidden:
        raise ValueError(f"Targeting system database '{name}' is strictly prohibited")
    # Disallow dangerous SQL characters
    if not re.match(r"^[a-zA-Z0-9_]+$", name):
        raise ValueError(f"Database name '{name}' contains illegal characters")


def _resolve_base_pg_url(base_url: str | None = None) -> str:
    """Resolve base PostgreSQL URL from argument or environment."""
    if base_url:
        return base_url

    test_url = os.getenv("TEST_DATABASE_URL")
    if test_url:
        return test_url

    # Check if TEST_POSTGRES_ADMIN_URL is set
    admin_env = os.getenv("TEST_POSTGRES_ADMIN_URL")
    if admin_env:
        return admin_env

    db_url = os.getenv("DATABASE_URL")
    if db_url:
        return db_url

    # Local fallback
    return f"postgresql+asyncpg://nexa:nexa_test@127.0.0.1:{_DEFAULT_LOCAL_PG_PORT}/postgres"


def postgres_admin_url(base_url: str | None = None) -> str:
    """Derive admin URL pointing to the 'postgres' maintenance DB from base URL."""
    raw = _resolve_base_pg_url(base_url)
    async_url = normalize_async_postgres_url(raw)
    require_loopback_postgres_url(async_url)

    parts = urlsplit(async_url)
    admin_parts = (parts.scheme, parts.netloc, "/postgres", parts.query, parts.fragment)
    return urlunsplit(admin_parts)


def postgres_database_url(db_name: str, base_url: str | None = None) -> str:
    """Derive application database URL for a named disposable database."""
    require_disposable_database_name(db_name)
    raw = _resolve_base_pg_url(base_url)
    async_url = normalize_async_postgres_url(raw)
    require_loopback_postgres_url(async_url)

    parts = urlsplit(async_url)
    db_parts = (parts.scheme, parts.netloc, f"/{db_name}", parts.query, parts.fragment)
    return urlunsplit(db_parts)


async def create_disposable_database(db_name: str, base_url: str | None = None) -> str:
    """Safely drop and recreate a disposable qualification database on loopback.

    Returns the async connection URL for the newly created database.
    """
    require_disposable_database_name(db_name)
    admin_url = postgres_admin_url(base_url)
    require_loopback_postgres_url(admin_url)

    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(text(f"DROP DATABASE IF EXISTS {db_name} WITH (FORCE)"))
            await conn.execute(text(f"CREATE DATABASE {db_name}"))
    finally:
        await engine.dispose()

    return postgres_database_url(db_name, base_url)


async def drop_disposable_database(db_name: str, base_url: str | None = None) -> None:
    """Safely drop a disposable qualification database on loopback with force."""
    require_disposable_database_name(db_name)
    admin_url = postgres_admin_url(base_url)
    require_loopback_postgres_url(admin_url)

    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(text(f"DROP DATABASE IF EXISTS {db_name} WITH (FORCE)"))
    finally:
        await engine.dispose()


def migrate_database_to_head(
    db_url: str,
    target_head: str = "20260906_verification_scheduler",
    alembic_ini_path: str = "alembic.ini",
) -> None:
    """Run Alembic upgrade to target head on the specified database URL."""
    require_loopback_postgres_url(db_url)
    sync_url = normalize_sync_postgres_url(db_url)
    async_url = normalize_async_postgres_url(db_url)
    cfg = Config(alembic_ini_path)
    cfg.set_main_option("sqlalchemy.url", sync_url)
    old_test_url = os.environ.get("TEST_DATABASE_URL")
    old_db_url = os.environ.get("DATABASE_URL")
    try:
        os.environ["TEST_DATABASE_URL"] = async_url
        os.environ["DATABASE_URL"] = async_url
        command.upgrade(cfg, target_head)
    finally:
        if old_test_url is not None:
            os.environ["TEST_DATABASE_URL"] = old_test_url
        else:
            os.environ.pop("TEST_DATABASE_URL", None)
        if old_db_url is not None:
            os.environ["DATABASE_URL"] = old_db_url
        else:
            os.environ.pop("DATABASE_URL", None)


def require_loopback_redis_url(url: str) -> None:
    """Validate that the Redis URL targets a loopback interface."""
    if not url:
        raise ValueError("Redis URL cannot be empty")
    parts = urlsplit(url)
    if parts.scheme not in {"redis", "rediss"}:
        raise ValueError(f"Unrecognized Redis scheme in: {url}")
    hostname = (parts.hostname or "").lower()
    if hostname not in _LOOPBACK_HOSTS:
        raise ValueError(
            f"Redis qualification URL must target a loopback host ({_LOOPBACK_HOSTS}), "
            f"got host: '{hostname}' in URL: '{url}'"
        )


def get_qualification_redis_url(base_url: str | None = None) -> str:
    """Resolve and validate loopback Redis qualification URL."""
    if base_url:
        url = base_url
    else:
        test_url = os.getenv("TEST_REDIS_URL")
        if test_url:
            url = test_url
        else:
            upstash = os.getenv("UPSTASH_REDIS_URL")
            if upstash:
                url = upstash
            else:
                url = f"redis://127.0.0.1:{_DEFAULT_LOCAL_REDIS_PORT}/0"

    require_loopback_redis_url(url)
    return url


async def seed_qualification_provider_trust(
    db: AsyncSession,
    *,
    provider_id: UUID,
    hospital_id: UUID,
    facility_code: str | None = None,
    roles: tuple[str, ...] | list[str] = ("clinician", "clinical_reviewer"),
    now: datetime | None = None,
    issue_session: bool = False,
    user_agent: str = "NexaClinicalSecurityTest/1.0",
    client_ip: str = "127.0.0.1",
) -> dict[str, Any]:
    """Seed authoritative provider, hospital, and affiliation trust models for qualification.

    Creates (if not already present):
    1. HospitalRegistry (active facility)
    2. FacilityVerification (status=VERIFIED)
    3. ProviderIdentity (status='active', is_active=True, email/phone verified)
    4. ProviderCredential (is_active=True, mfa_enabled=True)
    5. ProfessionalVerification (status=VERIFIED)
    6. ProviderHospitalAffiliation (trust_status=ACTIVE, is_active=True, specified roles)

    Optionally issues an active provider session token into Redis.
    """
    from app.models.provider import (
        AffiliationTrustStatus,
        AffiliationType,
        FacilityVerification,
        FacilityVerificationStatus,
        HospitalRegistry,
        ProfessionalVerification,
        ProfessionalVerificationStatus,
        ProviderCredential,
        ProviderHospitalAffiliation,
        ProviderIdentity,
    )

    if now is None:
        now = datetime.now(timezone.utc)

    resolved_facility_code = facility_code or f"QUAL-{hospital_id.hex[:10]}"

    hospital = await db.get(HospitalRegistry, hospital_id)
    if not hospital:
        hospital = HospitalRegistry(
            id=hospital_id,
            facility_code=resolved_facility_code,
            legal_name="Qualification Hospital",
            display_name="Qualification Hospital",
            country_code="IN",
            is_active=True,
        )
        facility = FacilityVerification(
            facility_id=hospital_id,
            status=FacilityVerificationStatus.VERIFIED.value,
            verified_at=now - timedelta(days=1),
            next_review_at=now + timedelta(days=1),
        )
        db.add_all((hospital, facility))
    else:
        fac_ver = await db.get(FacilityVerification, hospital_id)
        if not fac_ver:
            fac_ver = FacilityVerification(
                facility_id=hospital_id,
                status=FacilityVerificationStatus.VERIFIED.value,
                verified_at=now - timedelta(days=1),
                next_review_at=now + timedelta(days=1),
            )
            db.add(fac_ver)

    provider = await db.get(ProviderIdentity, provider_id)
    if not provider:
        provider = ProviderIdentity(
            id=provider_id,
            provider_uid=f"uid-{provider_id.hex[:10]}",
            status="active",
            is_active=True,
            contact_email=f"qual-{provider_id.hex[:8]}@example.test",
            contact_phone=f"+91{provider_id.int % 10000000000:010d}",
            email_verified_at=now - timedelta(days=1),
            phone_verified_at=now - timedelta(days=1),
        )
        cred = ProviderCredential(
            provider_id=provider_id,
            login_identifier=f"qual-{provider_id.hex[:8]}@example.test",
            password_hash="argon2-hash",
            is_active=True,
            mfa_enabled=True,
        )
        prof = ProfessionalVerification(
            provider_id=provider_id,
            status=ProfessionalVerificationStatus.VERIFIED.value,
            verified_at=now - timedelta(days=1),
            registration_valid_until=now + timedelta(days=30),
            next_review_at=now + timedelta(days=1),
        )
        db.add_all((provider, cred, prof))

    existing_affil = (
        await db.execute(
            select(ProviderHospitalAffiliation).where(
                ProviderHospitalAffiliation.provider_id == provider_id,
                ProviderHospitalAffiliation.hospital_id == hospital_id,
            )
        )
    ).scalar_one_or_none()

    if existing_affil:
        affil_id = existing_affil.id
        existing_affil.roles = list(roles)
        existing_affil.trust_status = AffiliationTrustStatus.ACTIVE.value
        existing_affil.is_active = True
    else:
        affil_id = uuid4()
        affil = ProviderHospitalAffiliation(
            id=affil_id,
            provider_id=provider_id,
            hospital_id=hospital_id,
            affiliation_type=AffiliationType.PERMANENT.value,
            roles=list(roles),
            is_primary=True,
            is_active=True,
            trust_status=AffiliationTrustStatus.ACTIVE.value,
            valid_from=now - timedelta(days=1),
            valid_until=now + timedelta(days=1),
        )
        db.add(affil)

    await db.flush()

    token = None
    headers: dict[str, str] = {}
    if issue_session:
        from app.services.provider_auth_service import issue_provider_session_token

        token = await issue_provider_session_token(
            provider_id=provider_id,
            user_agent=user_agent,
            client_ip=client_ip,
            mfa_verified_at=now,
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Hospital-Id": str(hospital_id),
            "User-Agent": user_agent,
        }

    return {
        "provider_id": provider_id,
        "hospital_id": hospital_id,
        "affiliation_id": affil_id,
        "token": token,
        "headers": headers,
    }
