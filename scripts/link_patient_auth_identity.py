#!/usr/bin/env python3
"""Admin-only CLI for provisioning a Supabase subject to a Nexa patient.

This script exposes no HTTP endpoint and never accepts or prints credentials.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.database import get_async_engine, get_session_factory  # noqa: E402
from app.models.patient import Patient  # noqa: E402
from app.models.patient_auth_identity import PatientAuthIdentity  # noqa: E402
from app.observability.audit_ledger import append_audit_log  # noqa: E402
from scripts.demo_environment import require_demo_environment  # noqa: E402


class ProvisioningConflict(RuntimeError):
    """The provider subject is already linked to another patient."""


class PatientNotEligible(RuntimeError):
    """The requested patient does not exist or has been deleted."""


async def link_patient_auth_identity(
    session: AsyncSession,
    *,
    patient_id: UUID,
    supabase_user_id: str,
) -> str:
    subject = supabase_user_id.strip()
    if not subject or len(subject) > 255:
        raise ValueError("Supabase user ID must contain between 1 and 255 characters")

    patient = await session.scalar(
        select(Patient).where(
            Patient.patient_uuid == patient_id,
            Patient.is_deleted.is_(False),
        )
    )
    if patient is None:
        raise PatientNotEligible("Patient does not exist or is deleted")

    existing = await session.scalar(
        select(PatientAuthIdentity).where(
            PatientAuthIdentity.provider == "supabase",
            PatientAuthIdentity.provider_subject == subject,
        )
    )
    if existing is not None:
        if existing.patient_id != patient_id:
            raise ProvisioningConflict("Supabase subject is linked to another patient")
        if existing.revoked_at is not None:
            raise ProvisioningConflict("Supabase subject mapping is revoked")
        return "already-linked"

    session.add(
        PatientAuthIdentity(
            patient_id=patient_id,
            provider="supabase",
            provider_subject=subject,
        )
    )
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        concurrent = await session.scalar(
            select(PatientAuthIdentity).where(
                PatientAuthIdentity.provider == "supabase",
                PatientAuthIdentity.provider_subject == subject,
            )
        )
        if concurrent is not None and concurrent.patient_id == patient_id and concurrent.revoked_at is None:
            return "already-linked"
        raise ProvisioningConflict("Supabase subject is linked to another patient") from None

    audited = await append_audit_log(
        actor_uid="admin-cli",
        event_type="PATIENT_AUTH_IDENTITY_LINKED",
        target_id=str(patient_id),
        status="success",
        metadata={"provider": "supabase"},
    )
    if not audited:
        await session.rollback()
        raise RuntimeError("Audit ledger write failed; mapping was not committed")
    await session.commit()
    return "linked"


async def _run(patient_id: UUID, supabase_user_id: str) -> int:
    session_factory = get_session_factory()
    try:
        async with session_factory() as session:
            result = await link_patient_auth_identity(
                session,
                patient_id=patient_id,
                supabase_user_id=supabase_user_id,
            )
        print(f"status={result} patient_id={patient_id} supabase_user_id={supabase_user_id}")
        return 0
    except (PatientNotEligible, ProvisioningConflict, ValueError, RuntimeError) as exc:
        print(f"status=rejected patient_id={patient_id} reason={exc}", file=sys.stderr)
        return 1
    finally:
        await get_async_engine().dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Link an existing patient to an existing Supabase Auth user")
    parser.add_argument("--patient-id", required=True, type=UUID)
    parser.add_argument("--supabase-user-id", required=True)
    args = parser.parse_args()
    require_demo_environment("link_patient_auth_identity")
    return asyncio.run(_run(args.patient_id, args.supabase_user_id))


if __name__ == "__main__":
    raise SystemExit(main())
