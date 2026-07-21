import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch, MagicMock
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.consent_engine import (
    issue_routine,
    issue_break_glass,
    ConsentPurpose,
    ConsentEngineUnavailable,
    AssuranceLevel
)

@pytest.mark.asyncio
async def test_issue_routine_happy_path():
    db = AsyncMock(spec=AsyncSession)
    patient_id = "patient-123"
    clinician_id = "doctor-456"
    purpose = ConsentPurpose.TREATMENT
    scope = ["clinical.diagnoses"]

    with patch("app.services.consent_engine.issue", new_callable=AsyncMock) as mock_issue, \
         patch("app.services.consent_engine.append_audit_log_or_503", new_callable=AsyncMock) as mock_audit, \
         patch("app.services.consent_engine.RedisAssuranceVerifier", new_callable=MagicMock) as mock_verifier_cls:
        
        mock_issue.return_value = "mock-token-123"
        mock_verifier = AsyncMock()
        mock_verifier.verify.return_value = MagicMock(verified=True)
        mock_verifier_cls.return_value = mock_verifier
        
        token = await issue_routine(
            patient_id=patient_id,
            clinician_id=clinician_id,
            purpose=purpose,
            scope=scope,
            db=db,
            assurance_level=AssuranceLevel.STANDARD,
            assurance_evidence={"foo": "bar"}
        )
        
        assert token == "mock-token-123"
        assert mock_issue.called
        assert mock_issue.call_args.kwargs["is_break_glass"] is False
        assert mock_issue.call_args.kwargs["assurance_level"] == AssuranceLevel.STANDARD
        assert mock_issue.call_args.kwargs["assurance_evidence"] == {"foo": "bar"}
        assert mock_audit.await_count == 2  # Before and after calls

@pytest.mark.asyncio
async def test_issue_routine_invalid_purpose_rejects():
    db = AsyncMock(spec=AsyncSession)
    with pytest.raises(ValueError, match="Invalid purpose"):
        await issue_routine(
            patient_id="p1",
            clinician_id="c1",
            purpose="INVALID",  # type: ignore
            scope=["*"],
            db=db
        )

@pytest.mark.asyncio
async def test_issue_break_glass_happy_path():
    db = AsyncMock(spec=AsyncSession)
    patient_id = "patient-123"
    clinician_id = "doctor-456"
    reason_code = "Life-threatening emergency"

    with patch("app.services.consent_engine.issue", new_callable=AsyncMock) as mock_issue, \
         patch("app.services.consent_engine.append_audit_log_or_503", new_callable=AsyncMock) as mock_audit, \
         patch("app.services.consent_engine.RedisAssuranceVerifier", new_callable=MagicMock) as mock_verifier_cls:
        
        mock_issue.return_value = "emergency-token-456"
        mock_verifier = AsyncMock()
        mock_verifier.verify.return_value = MagicMock(verified=True)
        mock_verifier_cls.return_value = mock_verifier
        
        token = await issue_break_glass(
            patient_id=patient_id,
            clinician_id=clinician_id,
            reason_code=reason_code,
            db=db,
            hospital_id="44444444-4444-4444-8444-444444444444",
            scope=["allergies"],
            reason_code_version="v1",
            session_binding="session-binding",
            mfa_verified_at=datetime.now(timezone.utc),
        )
        
        assert token == "emergency-token-456"
        assert mock_issue.called
        assert mock_issue.call_args.kwargs["is_break_glass"] is True
        assert mock_issue.call_args.kwargs["ttl_seconds"] == 900
        assert mock_issue.call_args.kwargs["scope"] == ["allergies"]
        assert mock_issue.call_args.kwargs["assurance_level"] == AssuranceLevel.BREAK_GLASS
        assert mock_audit.await_count == 2

@pytest.mark.asyncio
async def test_issue_break_glass_missing_reason_code_rejects():
    db = AsyncMock(spec=AsyncSession)
    with pytest.raises(ValueError, match="Break-glass grants require a non-empty reason_code"):
        await issue_break_glass(
            patient_id="p1",
            clinician_id="c1",
            reason_code="",
            db=db
        )

@pytest.mark.asyncio
async def test_issue_break_glass_enforces_15_min_ttl():
    db = AsyncMock(spec=AsyncSession)
    with patch("app.services.consent_engine.issue", new_callable=AsyncMock) as mock_issue, \
         patch("app.services.consent_engine.append_audit_log_or_503", new_callable=AsyncMock), \
         patch("app.services.consent_engine.RedisAssuranceVerifier", new_callable=MagicMock) as mock_verifier_cls:
        
        mock_verifier = AsyncMock()
        mock_verifier.verify.return_value = MagicMock(verified=True)
        mock_verifier_cls.return_value = mock_verifier

        await issue_break_glass(
            patient_id="p1",
            clinician_id="c1",
            reason_code="Emergency",
            db=db,
            hospital_id="44444444-4444-4444-8444-444444444444",
            scope=["allergies"],
            reason_code_version="v1",
            session_binding="session-binding",
            mfa_verified_at=datetime.now(timezone.utc),
        )
        assert mock_issue.call_args.kwargs["ttl_seconds"] == 900

@pytest.mark.asyncio
async def test_issue_routine_audit_failure_aborts_issuance():
    db = AsyncMock(spec=AsyncSession)
    with patch("app.services.consent_engine.append_audit_log_or_503", new_callable=AsyncMock) as mock_audit, \
         patch("app.services.consent_engine.RedisAssuranceVerifier", new_callable=MagicMock) as mock_verifier_cls:
        
        mock_verifier = AsyncMock()
        mock_verifier.verify.return_value = MagicMock(verified=True)
        mock_verifier_cls.return_value = mock_verifier

        mock_audit.side_effect = ConsentEngineUnavailable("Audit service down")
        
        with pytest.raises(ConsentEngineUnavailable):
            await issue_routine(
                patient_id="p1",
                clinician_id="c1",
                purpose=ConsentPurpose.TREATMENT,
                scope=["*"],
                db=db
            )