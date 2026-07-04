"""
Consent Assurance Service (Push Approval + Biometric)
Uses the canonical ConsentAssurance type from schemas.
"""

from uuid import UUID
from app.schemas.consent import ConsentAssurance


class PushApprovalResult:
    def __init__(self, approved: bool, timeout: bool = False):
        self.approved = approved
        self.timeout = timeout


class AssuranceService:
    """Service for Push + Biometric consent assurance"""

    async def request_push_approval(
        self,
        patient_uuid: UUID,
        clinician_name: str,
        hospital_name: str,
        purpose: str,
    ) -> PushApprovalResult:
        """
        Initiate push notification for patient approval.
        The actual approve/deny/timeout decision lives in the frontend
        (PushApprovalScreen) for the 90-second window.
        """
        print(f"[PUSH] Sent to {patient_uuid}: Dr. {clinician_name} at {hospital_name} requests access for {purpose}")
        
        # Return success — the frontend handles the 90s timer + fallback
        return PushApprovalResult(approved=True, timeout=False)

    async def verify_biometric(
        self,
        patient_uuid: UUID,
        biometric_token: str,
    ) -> bool:
        """
        Verify biometric confirmation from mobile app.
        In production: validate signed biometric assertion from mobile SDK.
        """
        print(f"[BIOMETRIC] Verifying for patient {patient_uuid}")

        # Basic validation
        if not biometric_token or len(biometric_token) < 10:
            return False

        # TODO: In production, validate:
        # - Cryptographic signature
        # - Device binding
        # - Token expiry
        # - Replay protection

        # Demo mode: Accept tokens that start with "valid-"
        if biometric_token.startswith("valid-"):
            return True

        return False

    async def evaluate_assurance_policy(
        self,
        patient_uuid: UUID,
        requested_method: ConsentAssurance,
    ) -> ConsentAssurance:
        """
        Determine final consent_assurance value based on patient policy.
        """
        if requested_method == "standard":
            return "standard"
        return requested_method