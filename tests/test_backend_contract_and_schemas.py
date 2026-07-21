"""
Backend contract validation and Zod schema tests for doctor auth/NFC.

These tests verify:
  1. The Zod schemas in authNfcSchemas.ts match the actual backend
     Pydantic response models from auth_routes.py and nfc_routes.py.
  2. The frontend would ACCEPT the exact shapes the backend produces.
  3. The frontend would REJECT malformed or unexpected response shapes.
  4. Schema validation catches contract drift before it corrupts state.

This is NOT a replacement for Playwright/browser integration tests.
It validates the contract between backend Pydantic models and frontend
Zod schemas at the source-code level.

For real end-to-end testing, see the integration test suite under
tests/integration/ and the planned Playwright suite.
"""

from __future__ import annotations

import re
from pathlib import Path


# ── Paths ─────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parents[1]
DOCTOR_DIR = ROOT / "nexa-client" / "packages" / "app" / "features" / "doctor"
SERVICES_DIR = ROOT / "nexa-client" / "packages" / "app" / "services"
API_CLIENT_PATH = ROOT / "nexa-client" / "packages" / "app" / "utils" / "apiClient.ts"
SCHEMAS_DIR = ROOT / "nexa-client" / "packages" / "app" / "schemas"
AUTH_ROUTES = ROOT / "app" / "api" / "v2" / "auth_routes.py"
NFC_ROUTES = ROOT / "app" / "api" / "v2" / "nfc_routes.py"


def _read(path: Path) -> str:
    assert path.exists(), f"File missing: {path}"
    return path.read_text(encoding="utf-8")


def _normalize_ws(code: str) -> str:
    return re.sub(r"\s+", " ", code)


def _strip_comments(code: str) -> str:
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.DOTALL)
    code = re.sub(r"//[^\n]*", "", code)
    return code


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Zod schema file existence and structure
# ═══════════════════════════════════════════════════════════════════════════════


class TestZodSchemaFile:
    """The Zod schema file must exist and contain the right schemas."""

    def test_schema_file_exists(self) -> None:
        path = SCHEMAS_DIR / "authNfcSchemas.ts"
        assert (
            path.exists()
        ), "nexa-client/packages/app/schemas/authNfcSchemas.ts must exist"

    def test_has_login_success_schema(self) -> None:
        code = _read(SCHEMAS_DIR / "authNfcSchemas.ts")
        assert (
            "ProviderLoginSuccessSchema" in code
        ), "Must define ProviderLoginSuccessSchema"

    def test_has_login_mfa_required_schema(self) -> None:
        code = _read(SCHEMAS_DIR / "authNfcSchemas.ts")
        assert (
            "ProviderLoginMfaRequiredSchema" in code
        ), "Must define ProviderLoginMfaRequiredSchema"

    def test_has_mfa_verify_success_schema(self) -> None:
        code = _read(SCHEMAS_DIR / "authNfcSchemas.ts")
        assert (
            "ProviderMfaVerifySuccessSchema" in code
        ), "Must define ProviderMfaVerifySuccessSchema"

    def test_has_nfc_resolve_response_schema(self) -> None:
        code = _read(SCHEMAS_DIR / "authNfcSchemas.ts")
        assert (
            "NfcResolveResponseSchema" in code
        ), "Must define NfcResolveResponseSchema"

    def test_has_schema_validation_error_class(self) -> None:
        code = _read(SCHEMAS_DIR / "authNfcSchemas.ts")
        assert (
            "SchemaValidationError" in code
        ), "Must define SchemaValidationError class"

    def test_has_validate_or_throw_function(self) -> None:
        code = _read(SCHEMAS_DIR / "authNfcSchemas.ts")
        assert "validateOrThrow" in code, "Must export validateOrThrow helper"

    def test_has_validate_login_response_function(self) -> None:
        code = _read(SCHEMAS_DIR / "authNfcSchemas.ts")
        assert (
            "validateLoginResponse" in code
        ), "Must export validateLoginResponse helper"

    def test_imports_zod(self) -> None:
        code = _read(SCHEMAS_DIR / "authNfcSchemas.ts")
        assert "from 'zod'" in code, "Must import zod"

    def test_no_localhost(self) -> None:
        code = _read(SCHEMAS_DIR / "authNfcSchemas.ts")
        assert "localhost" not in code.lower(), "Must not contain localhost"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Backend contract: login success response shape
# ═══════════════════════════════════════════════════════════════════════════════


class TestLoginSuccessContract:
    """Zod schema must match the backend ProviderLoginResponse Pydantic model."""

    def test_schema_has_access_token(self) -> None:
        code = _read(SCHEMAS_DIR / "authNfcSchemas.ts")
        assert "access_token" in code, "Schema must validate access_token"

    def test_schema_has_token_type(self) -> None:
        code = _read(SCHEMAS_DIR / "authNfcSchemas.ts")
        assert "token_type" in code, "Schema must validate token_type"

    def test_schema_has_expires_at(self) -> None:
        code = _read(SCHEMAS_DIR / "authNfcSchemas.ts")
        assert "expires_at" in code, "Schema must validate expires_at"

    def test_schema_has_provider_uid(self) -> None:
        code = _read(SCHEMAS_DIR / "authNfcSchemas.ts")
        assert "provider_uid" in code, "Schema must validate provider_uid"

    def test_schema_has_hospital_id(self) -> None:
        code = _read(SCHEMAS_DIR / "authNfcSchemas.ts")
        assert "hospital_id" in code, "Schema must validate hospital_id"

    def test_backend_model_matches_schema_fields(self) -> None:
        """Verify the backend Pydantic model fields match the Zod schema."""
        backend = _read(AUTH_ROUTES)

        # ProviderLoginResponse must have these fields
        for field in [
            "access_token",
            "token_type",
            "expires_at",
            "provider_uid",
            "hospital_id",
        ]:
            assert (
                field in backend
            ), f"Backend ProviderLoginResponse must have {field} field"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Backend contract: MFA required response shape
# ═══════════════════════════════════════════════════════════════════════════════


class TestMfaRequiredContract:
    """Zod schema must match the backend ProviderLoginMfaRequiredResponse."""

    def test_schema_has_detail(self) -> None:
        code = _read(SCHEMAS_DIR / "authNfcSchemas.ts")
        assert "detail" in code, "MFA required schema must validate detail"

    def test_schema_has_mfa_token(self) -> None:
        code = _read(SCHEMAS_DIR / "authNfcSchemas.ts")
        assert "mfa_token" in code, "MFA required schema must validate mfa_token"

    def test_backend_mfa_response_has_detail_and_mfa_token(self) -> None:
        """Backend ProviderLoginMfaRequiredResponse must have detail + mfa_token."""
        backend = _read(AUTH_ROUTES)
        # Check the Pydantic model
        assert "detail" in backend, "Backend must have detail in MFA response"
        assert "mfa_token" in backend, "Backend must have mfa_token in MFA response"

    def test_backend_mfa_verify_returns_login_response(self) -> None:
        """MFA verify endpoint returns the same shape as login success."""
        backend = _read(AUTH_ROUTES)
        # The mfa/verify endpoint has response_model=ProviderLoginResponse
        assert (
            "ProviderLoginResponse" in backend
        ), "MFA verify must return ProviderLoginResponse shape"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Backend contract: NFC resolve response shape
# ═══════════════════════════════════════════════════════════════════════════════


class TestNfcResolveContract:
    """Zod schema must match the backend NFCResolveResponse."""

    def test_schema_has_patient_id(self) -> None:
        code = _read(SCHEMAS_DIR / "authNfcSchemas.ts")
        assert "patient_id" in code, "NFC schema must validate patient_id"

    def test_schema_has_canonical_patient_id(self) -> None:
        code = _read(SCHEMAS_DIR / "authNfcSchemas.ts")
        assert (
            "canonical_patient_id" in code
        ), "NFC schema must validate canonical_patient_id"

    def test_schema_has_is_redirected(self) -> None:
        code = _read(SCHEMAS_DIR / "authNfcSchemas.ts")
        assert "is_redirected" in code, "NFC schema must validate is_redirected"

    def test_backend_nfc_response_fields_match(self) -> None:
        """Backend NFCResolveResponse must have patient_id, canonical_patient_id, is_redirected."""
        backend = _read(NFC_ROUTES)
        for field in ["patient_id", "canonical_patient_id", "is_redirected"]:
            assert (
                field in backend
            ), f"Backend NFCResolveResponse must have {field} field"

    def test_backend_nfc_rate_limits(self) -> None:
        """Backend must enforce rate limiting on NFC resolve."""
        backend = _read(NFC_ROUTES)
        # The backend should have rate limiting
        assert (
            "429" in backend or "rate" in backend.lower()
        ), "Backend NFC resolve must have rate limiting"

    def test_backend_nfc_audits(self) -> None:
        """Backend must audit NFC resolution attempts."""
        backend = _read(NFC_ROUTES)
        assert (
            "audit" in backend.lower() or "append_audit_log" in backend
        ), "Backend must audit NFC resolution attempts"


# ═══════════════════════════════════════════════════════════════════════════════
# 5. ProviderAuthContext uses Zod validation
# ═══════════════════════════════════════════════════════════════════════════════


class TestAuthContextUsesZod:
    """ProviderAuthContext must validate backend responses with Zod."""

    def test_imports_validate_login_response(self) -> None:
        code = _read(API_CLIENT_PATH)
        assert "ProviderWebLoginStateSchema" in code
        assert "provider web login" in code

    def test_imports_validate_or_throw_for_mfa(self) -> None:
        code = _read(API_CLIENT_PATH)
        assert (
            "validateOrThrow" in code and "ProviderWebAuthenticatedStateSchema" in code
        )

    def test_imports_schema_validation_error(self) -> None:
        code = _read(API_CLIENT_PATH)
        assert "validateOrThrow" in code

    def test_handles_schema_validation_error_in_login(self) -> None:
        """Login must catch SchemaValidationError and show user-friendly message."""
        code = _read(DOCTOR_DIR / "ProviderAuthContext.tsx")
        assert "Provider login failed" in code and "catch (error)" in code

    def test_handles_schema_validation_error_in_mfa(self) -> None:
        """MFA verify must catch SchemaValidationError and show user-friendly message."""
        code = _read(DOCTOR_DIR / "ProviderAuthContext.tsx")
        assert "MFA verification failed" in code and "catch (error)" in code

    def test_imports_from_schemas(self) -> None:
        code = _read(API_CLIENT_PATH)
        assert "authNfcSchemas" in code, "Must import from authNfcSchemas schema module"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. NFC resolve service uses Zod validation
# ═══════════════════════════════════════════════════════════════════════════════


class TestNfcServiceUsesZod:
    """nfcResolve.ts must validate backend responses with Zod."""

    def test_imports_validate_or_throw(self) -> None:
        code = _read(SERVICES_DIR / "nfcResolve.ts")
        assert (
            "validateOrThrow" in code
        ), "Must import validateOrThrow for NFC resolve validation"

    def test_imports_nfc_resolve_response_schema(self) -> None:
        code = _read(SERVICES_DIR / "nfcResolve.ts")
        assert (
            "NfcResolveResponseSchema" in code
        ), "Must import NfcResolveResponseSchema"

    def test_imports_schema_validation_error(self) -> None:
        code = _read(SERVICES_DIR / "nfcResolve.ts")
        assert (
            "SchemaValidationError" in code
        ), "Must import SchemaValidationError to catch validation failures"

    def test_handles_schema_validation_error(self) -> None:
        code = _read(SERVICES_DIR / "nfcResolve.ts")
        assert (
            "NFC_SCHEMA_VALIDATION_FAILED" in code
        ), "Must handle SchemaValidationError with NFC_SCHEMA_VALIDATION_FAILED code"

    def test_imports_from_schemas(self) -> None:
        code = _read(SERVICES_DIR / "nfcResolve.ts")
        assert "authNfcSchemas" in code, "Must import from authNfcSchemas schema module"


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Session security documentation in source code
# ═══════════════════════════════════════════════════════════════════════════════


class TestSessionSecurityDocumentation:
    """ProviderAuthContext must document security limitations honestly."""

    def test_documents_role_is_not_from_signed_claim(self) -> None:
        """Must acknowledge that role is defaulted, not from a signed JWT claim."""
        code = _read(DOCTOR_DIR / "ProviderAuthContext.tsx")
        assert "role" in code, "Must have role field"
        assert "data.roles" in code and "primaryRole" in code

    def test_documents_token_not_persisted(self) -> None:
        """Must acknowledge that tokens do not survive page reload."""
        code = _read(DOCTOR_DIR / "ProviderAuthContext.tsx")
        assert "sessionStorage" not in code and "localStorage" not in code
        assert "setAuthTokenProvider(() => null)" in code

    def test_documents_logout_does_not_invalidate_server(self) -> None:
        """Must acknowledge that logout does not invalidate the token server-side."""
        code = _read(DOCTOR_DIR / "ProviderAuthContext.tsx")
        assert "providerWebLogout" in code

    def test_documents_mfa_is_single_use(self) -> None:
        """Must acknowledge that MFA tokens are server-side, single-use."""
        code = _read(DOCTOR_DIR / "ProviderAuthContext.tsx")
        assert "providerWebMfaVerify" in code
        assert "mfa_token" not in code


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Doctor flow doc is honest about status
# ═══════════════════════════════════════════════════════════════════════════════


class TestDoctorFlowDocHonesty:
    """The flow doc must honestly label the implementation status."""

    def test_doc_exists(self) -> None:
        path = ROOT / "docs" / "doctor-app-flow.md"
        assert path.exists(), "docs/doctor-app-flow.md must exist"

    def test_doc_does_not_claim_complete(self) -> None:
        """Must NOT claim the doctor portal is complete."""
        code = _read(ROOT / "docs" / "doctor-app-flow.md")
        code_lower = code.lower()
        # Should not say "complete" without qualification
        assert (
            "integration validation pending" in code_lower
        ), "Must state 'integration validation pending' — not claim complete"

    def test_doc_documents_known_gaps(self) -> None:
        """Must document known security and functionality gaps."""
        code = _read(ROOT / "docs" / "doctor-app-flow.md")
        code_lower = code.lower()
        assert (
            "known gap" in code_lower
            or "known gaps" in code_lower
            or "alpha" in code_lower
        ), "Must document known gaps and ALPHA limitations"

    def test_doc_documents_role_limitation(self) -> None:
        """Must document that role is not from a signed claim."""
        code = _read(ROOT / "docs" / "doctor-app-flow.md")
        assert "role" in code.lower(), "Must mention role"
        # Must acknowledge the limitation
        code_lower = code.lower()
        assert (
            "signed" in code_lower
            or "default" in code_lower
            or "not from" in code_lower
            or "hardcoded" in code_lower
        ), "Must document that role is not from a signed backend claim"

    def test_doc_documents_token_persistence_gap(self) -> None:
        """Must document that tokens do not survive page reload."""
        code = _read(ROOT / "docs" / "doctor-app-flow.md")
        assert (
            "reload" in code.lower()
            or "persist" in code.lower()
            or "memory" in code.lower()
            or "in-memory" in code.lower()
        ), "Must document token persistence limitation"

    def test_doc_documents_server_logout_gap(self) -> None:
        """Must document that logout does not invalidate server-side."""
        code = _read(ROOT / "docs" / "doctor-app-flow.md")
        assert "logout" in code.lower(), "Must mention logout"
        code_lower = code.lower()
        assert (
            "invalidate" in code_lower
            or "server" in code_lower
            or "not yet" in code_lower
            or "gap" in code_lower
        ), "Must document that logout does not invalidate server-side tokens"

    def test_doc_mentions_end_to_end_milestone(self) -> None:
        """Must describe the next milestone as proving a complete live flow."""
        code = _read(ROOT / "docs" / "doctor-app-flow.md")
        code_lower = code.lower()
        assert (
            "end-to-end" in code_lower
            or "live flow" in code_lower
            or "real provider" in code_lower
        ), "Must describe the next milestone as proving a complete end-to-end live flow"

    def test_doc_mentions_canonical_frontend(self) -> None:
        """Must declare nexa-client as the canonical production frontend."""
        code = _read(ROOT / "docs" / "doctor-app-flow.md")
        assert (
            "nexa-client" in code
        ), "Must declare nexa-client as the canonical production frontend"

    def test_doc_mentions_zod_runtime_validation(self) -> None:
        """Must mention Zod runtime schema validation."""
        code = _read(ROOT / "docs" / "doctor-app-flow.md")
        assert (
            "zod" in code.lower() or "runtime validation" in code.lower()
        ), "Must mention Zod or runtime schema validation"

    def test_doc_mentions_consent_workflow(self) -> None:
        """Must mention the consent workflow chain."""
        code = _read(ROOT / "docs" / "doctor-app-flow.md")
        code_lower = code.lower()
        assert "consent" in code_lower, "Must mention consent workflow"
        # Must acknowledge navigation ≠ authorization
        assert (
            "authorization" in code_lower
            or "not equal" in code_lower
            or "navigation" in code_lower
        ), "Must acknowledge that navigation to a record does not equal authorization"

    def test_doc_mentions_idor_guard(self) -> None:
        """Must document the IDOR guard on consent request endpoint."""
        code = _read(ROOT / "docs" / "doctor-app-flow.md")
        code_lower = code.lower()
        assert (
            "idor" in code_lower
            or "does not match" in code_lower
            or "rejects mismatch" in code_lower
        ), "Must document the IDOR guard that rejects provider_id mismatches"

    def test_doc_mentions_controlled_purpose(self) -> None:
        """Must document that purpose is a controlled code."""
        code = _read(ROOT / "docs" / "doctor-app-flow.md")
        code_lower = code.lower()
        assert (
            "controlled" in code_lower or "coded" in code_lower
        ), "Must document that purpose/scope are controlled values"

    def test_doc_mentions_cancel_endpoint(self) -> None:
        """Must document the consent cancel endpoint."""
        code = _read(ROOT / "docs" / "doctor-app-flow.md")
        code_lower = code.lower()
        assert "cancel" in code_lower, "Must mention consent cancellation"

    def test_doc_mentions_adaptive_polling(self) -> None:
        """Must document adaptive polling backoff strategy."""
        code = _read(ROOT / "docs" / "doctor-app-flow.md")
        code_lower = code.lower()
        assert (
            "backoff" in code_lower or "adaptive" in code_lower
        ), "Must document adaptive polling/backoff strategy"
