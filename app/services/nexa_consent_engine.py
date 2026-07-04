"""
Nexa Care v1.0 Consent Engine
Implements the architecture from the final draft:
- Layered consent assurance (Standard / Push / Biometric)
- Break-glass support
- Immutable consent ledger
"""

from datetime import datetime, timedelta, timezone
from typing import Optional, Literal
from uuid import UUID
import secrets

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, insert

from app.models.consent_ledger import ConsentLedger
from app.models.consent_sessions import ConsentSession
from app.observability.audit_ledger import append_audit_log


ConsentAssuranceType = Literal[
    "standard", "push_approved", "biometric_confirmed", "bypassed_emergency"
]


class NexaConsentEngine:
    """Core consent authority for Nexa Care v1.0"""

    DEFAULT_TTL_MINUTES = 30
    BREAK_GLASS_TTL_MINUTES = 15

    async def issue_routine_consent(
        self,
        db: AsyncSession,
        *,
        patient_uuid: UUID,
        hospital_id: str,
        clinician_id: str,
        purpose: str,
        consent_assurance: ConsentAssuranceType = "standard",
    ) -> str:
        """Issue a standard consent token"""
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=self.DEFAULT_TTL_MINUTES)

        # Write to immutable consent ledger
        ledger_entry = ConsentLedger(
            patient_uuid=patient_uuid,
            hospital_id=hospital_id,
            clinician_id=clinician_id,
            purpose=purpose,
            consent_assurance=consent_assurance,
            granted_at=now,
            expires_at=expires_at,
        )
        db.add(ledger_entry)

        # Create live session
        session = ConsentSession(
            patient_uuid=patient_uuid,
            consent_token=token,
            purpose=purpose,
            consent_assurance=consent_assurance,
            issued_at=now,
            expires_at=expires_at,
            hospital_id=hospital_id,
            clinician_id=clinician_id,
        )
        db.add(session)
        await db.commit()

        await append_audit_log(
            actor_uid=clinician_id,
            event_type="CONSENT_ISSUED",
            target_id=str(patient_uuid),
            status="SUCCESS",
            metadata={"purpose": purpose, "assurance": consent_assurance},
        )

        return token

    async def issue_break_glass(
        self,
        db: AsyncSession,
        *,
        patient_uuid: UUID,
        hospital_id: str,
        clinician_id: str,
        reason: str,
        justification: str,
    ) -> str:
        """Emergency break-glass access (bypasses normal assurance)"""
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=self.BREAK_GLASS_TTL_MINUTES)

        ledger_entry = ConsentLedger(
            patient_uuid=patient_uuid,
            hospital_id=hospital_id,
            clinician_id=clinician_id,
            purpose="EMERGENCY",
            consent_assurance="bypassed_emergency",
            granted_at=now,
            expires_at=expires_at,
        )
        db.add(ledger_entry)

        session = ConsentSession(
            patient_uuid=patient_uuid,
            consent_token=token,
            purpose="EMERGENCY",
            consent_assurance="bypassed_emergency",
            issued_at=now,
            expires_at=expires_at,
            hospital_id=hospital_id,
            clinician_id=clinician_id,
        )
        db.add(session)
        await db.commit()

        await append_audit_log(
            actor_uid=clinician_id,
            event_type="BREAK_GLASS_ISSUED",
            target_id=str(patient_uuid),
            status="SUCCESS",
            metadata={"reason": reason, "justification": justification},
        )

        return token

    async def validate_consent(
        self,
        db: AsyncSession,
        token: str,
        patient_uuid: Optional[UUID] = None,
    ) -> Optional[dict]:
        """Validate a consent token"""
        stmt = select(ConsentSession).where(
            ConsentSession.consent_token == token,
            ConsentSession.revoked_at.is_(None),
            ConsentSession.expires_at > datetime.now(timezone.utc),
        )
        result = await db.execute(stmt)
        session = result.scalar_one_or_none()

        if not session:
            return None

        if patient_uuid and session.patient_uuid != patient_uuid:
            return None

        return {
            "patient_uuid": str(session.patient_uuid),
            "purpose": session.purpose,
            "consent_assurance": session.consent_assurance,
            "expires_at": session.expires_at.isoformat(),
        }