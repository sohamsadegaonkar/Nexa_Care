"""Server-owned policies shared by interactive and delegated clinical gates."""

from app.services.clinical_eligibility import ContactAssurancePolicy


# Repository-owner approved Nexa Care product/security policy. This is not a
# statutory claim and may not silently fall back to email-only assurance.
CLINICAL_CONTACT_ASSURANCE_POLICY = ContactAssurancePolicy(
    require_email_verified=True,
    require_phone_verified=True,
    version="clinical-contact-email-and-phone/v1",
)
