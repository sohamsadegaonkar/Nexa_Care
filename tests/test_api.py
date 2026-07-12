"""
End-to-end integration test for Nexa Care.

Why this file was rewritten entirely (not patched):

  1. AUTH SCHEME MISMATCH. The old version sent `X-Provider-Key` and set
     `PROVIDER_API_KEY` in os.environ. That backed app/api/auth_deps.py,
     which has been deleted. Every provider route now authenticates via
     get_provider_context() (app/core/dependencies.py), which validates
     credentials against provider_credential and resolves hospital
     affiliation — not a shared facility API key.

  2. SESSION MODEL MISMATCH. The old version posted a `masked_internal_id`
     in the /request-consent body. That was the exact IDOR this system was
     rebuilt to close: /request-consent (like GET /api/v1/record) now
     resolves its patient ONLY from a handshake-derived session token via
     Depends(get_scoped_session) -- never from the request body.

  3. RESPONSE SHAPE MISMATCH. The old version asserted
     view_data["pii"]["phone"] and view_data["clinical"]["diagnoses"].
     The old combined GET /view-record has since been split into
     GET /view-record/clinical and GET /view-record/pii so the consent
     view path never joins identity and clinical shards in one response.

  4. INFRASTRUCTURE MISMATCH. As a plain pytest test hitting the real
     get_supabase_client()/get_redis_client(), this could never pass in
     CI anyway -- ci.yml only sets placeholder Supabase/Redis URLs, with
     no live database behind them. Every other test file in this suite
     mocks those two functions; this one didn't, which is why it needed
     a real instance to mean anything.

This version fixes all four: it exercises the actual current auth model
end-to-end (provider session token -> enroll -> handshake -> session
Bearer token -> consent token -> redacted view), and replaces Supabase /
Redis with tiny in-memory fakes (see FakeSupabaseClient / FakeRedisClient
below) that implement only the exact call chains the app code issues --
not a full reimplementation of either service, just enough surface area
to drive the real FastAPI app, real dependency graph, and real
middleware through a full request lifecycle without live infrastructure.
"""
from __future__ import annotations

import os
import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

# Required config must exist before app.main is imported -- the FastAPI
# lifespan calls get_*_config() at startup, and those raise ConfigError on
# a missing var.
os.environ.setdefault("SUPABASE_URL", "https://placeholder.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "placeholder-key")
os.environ.setdefault("UPSTASH_REDIS_URL", "redis://localhost:6379")
os.environ.setdefault("HANDSHAKE_PEPPER_SECRET", "test-pepper-secret")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://test:test@localhost:5432/nexa_test",
)

from fastapi.testclient import TestClient  # noqa: E402

from app.core.database import get_db_session  # noqa: E402
from app.core.dependencies import get_provider_context  # noqa: E402
from app.main import app  # noqa: E402
from app.models.provider import AffiliationType  # noqa: E402
from app.models.provider_context import (  # noqa: E402
    AffiliationContext,
    HospitalContext,
    ProviderContext,
    ProviderIdentityContext,
)
from app.services.provider_auth_service import ProviderAuthFailure, ProviderAuthResult  # noqa: E402


def _test_provider_context() -> ProviderContext:
    return ProviderContext(
        provider=ProviderIdentityContext(
            provider_id=uuid.uuid4(),
            display_name="Dr. Integration Test",
            medical_registration_number="MCI-99999",
            specialty="General Medicine",
            contact_email="integration@example.com",
        ),
        hospital=HospitalContext(
            hospital_id=uuid.uuid4(),
            facility_code="HOSP-TEST",
            display_name="Integration Test Hospital",
        ),
        affiliation=AffiliationContext(
            affiliation_id=uuid.uuid4(),
            affiliation_type=AffiliationType.PERMANENT,
            department="General Medicine",
            roles=["clinician"],
            is_primary=True,
            valid_from=None,
            valid_until=None,
        ),
    )


PROVIDER_CONTEXT = _test_provider_context()
PROVIDER_HEADERS = {
    "Authorization": "Bearer valid-provider-session",
    "X-Hospital-Id": str(PROVIDER_CONTEXT.hospital.hospital_id),
}


async def _override_provider_context() -> ProviderContext:
    return PROVIDER_CONTEXT


async def _mock_db_session():
    db = AsyncMock()
    # db.add is synchronous in SQLAlchemy; AsyncMock would otherwise make it
    # a coroutine and emit a runtime warning when not awaited.
    db.add = lambda row: None
    db.commit = AsyncMock()
    db.execute = AsyncMock()
    db.rollback = AsyncMock()
    yield db


# ─────────────────────────────────────────────────────────────────────────
# Minimal in-memory fakes for the Supabase / Redis layers.
# ─────────────────────────────────────────────────────────────────────────

class _FakeResult:
    def __init__(self, data=None, error=None):
        self.data = data
        self.error = error


class _FakeTableQuery:
    """Mimics exactly the subset of the postgrest-py fluent API this
    codebase calls: .insert / .select / .eq / .single / .limit / .order /
    .execute(). Backed by a plain dict-of-lists in memory -- not a real
    query engine, just enough to satisfy these specific call shapes."""

    def __init__(self, store: dict[str, list[dict]], table_name: str):
        self._store = store
        self._table_name = table_name
        self._mode: str | None = None
        self._insert_payload: dict | None = None
        self._select_cols: str | None = None
        self._filters: list[tuple[str, str]] = []
        self._single = False
        self._limit: int | None = None
        self._order_col: str | None = None
        self._order_desc = False

    def insert(self, payload: dict):
        self._mode = "insert"
        self._insert_payload = payload
        return self

    def select(self, cols: str = "*"):
        self._mode = "select"
        self._select_cols = cols
        return self

    def eq(self, col: str, val):
        self._filters.append((col, str(val)))
        return self

    def single(self):
        self._single = True
        return self

    def limit(self, n: int):
        self._limit = n
        return self

    def order(self, col: str, desc: bool = False):
        self._order_col = col
        self._order_desc = desc
        return self

    def execute(self):
        rows = self._store.setdefault(self._table_name, [])

        if self._mode == "insert":
            row = dict(self._insert_payload or {})
            row.setdefault("id", str(uuid.uuid4()))
            row.setdefault("created_at", datetime.now(timezone.utc).isoformat())
            rows.append(row)
            return _FakeResult(data=[row], error=None)

        # ── select ───────────────────────────────────────────────────
        matched = rows
        for col, val in self._filters:
            matched = [r for r in matched if str(r.get(col)) == val]

        if self._order_col:
            matched = sorted(
                matched, key=lambda r: r.get(self._order_col) or "", reverse=self._order_desc
            )

        if self._limit is not None:
            matched = matched[: self._limit]

        if self._select_cols and self._select_cols != "*":
            cols = [c.strip() for c in self._select_cols.split(",")]
            matched = [{c: r.get(c) for c in cols} for r in matched]

        if self._single:
            return _FakeResult(data=(matched[0] if matched else None), error=None)

        return _FakeResult(data=matched, error=None)


class FakeSupabaseClient:
    """Shared in-memory stand-in for app.core.supabase.get_supabase_client().
    One instance is reused across the whole lifecycle test so
    register -> enroll -> handshake -> consent -> split view-record
    endpoints all see consistent state, exactly like a real Postgres
    connection would."""

    def __init__(self):
        self._store: dict[str, list[dict]] = {}

    def table(self, name: str) -> _FakeTableQuery:
        return _FakeTableQuery(self._store, name)


class FakeRedisClient:
    """Shared in-memory stand-in for app.core.redis.get_redis_client()."""

    def __init__(self):
        self._store: dict[str, str] = {}

    def get(self, key):
        return self._store.get(key)

    def set(self, name, value, ex=None):
        self._store[name] = value
        return True

    def setex(self, name, time, value):
        self._store[name] = value
        return True

    def delete(self, name):
        self._store.pop(name, None)
        return 1

    def rpush(self, key, value):
        self._store.setdefault(key, []).append(value)  # type: ignore[assignment]
        return 1

    def ping(self):
        return True


class FakeAsyncConnection:
    """In-memory async connection for the health check SELECT 1 probe."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def execute(self, statement):
        return None


class FakeAsyncEngine:
    """In-memory stand-in for get_async_engine()."""

    def connect(self):
        return FakeAsyncConnection()


class AsyncFakeRedisClient:
    """In-memory stand-in for the async redis client used by ConsentEngine."""

    def __init__(self):
        self._store: dict[str, str | list[str]] = {}

    async def get(self, key):
        value = self._store.get(key)
        return value if isinstance(value, str) else None

    async def getdel(self, key):
        return self._store.pop(key, None)

    async def set(self, name, value, ex=None):
        self._store[name] = value
        return True

    async def setex(self, name, time, value):
        self._store[name] = value
        return True

    async def delete(self, name):
        self._store.pop(name, None)
        return 1

    async def rpush(self, key, value):
        current = self._store.get(key)
        if isinstance(current, list):
            current.append(value)
        else:
            self._store[key] = [value]
        return 1

    async def ping(self):
        return True


class FakeEncryptedField:
    def __init__(self, field_name: str, plaintext: str) -> None:
        self.field_name = field_name
        self.plaintext = plaintext

    def serialize(self) -> str:
        return self.plaintext


class FakeKMSProvider:
    async def generate_dek(self, patient_id: str, db) -> AsyncMock:
        await db.commit()
        return AsyncMock(dek_version=1)

    async def encrypt_field(self, patient_id: str, field_name: str, plaintext: str, db) -> FakeEncryptedField:
        return FakeEncryptedField(field_name, plaintext)


class TestNexaCareLifecycle(unittest.TestCase):
    """Drives the full provider -> patient -> consent lifecycle through the
    real app, with only the database/cache layers faked out."""

    @classmethod
    def setUpClass(cls):
        cls.fake_supabase = FakeSupabaseClient()
        cls.fake_redis = FakeRedisClient()
        cls.fake_async_redis = AsyncFakeRedisClient()

        # Every module that does `from app.core.X import get_Y_client` holds
        # its OWN reference to that function -- patching
        # app.core.supabase.get_supabase_client alone would not reach
        # app.api.routes' already-imported name, for example. Each
        # consuming module is patched individually, mirroring the pattern
        # already used in test_audit_ledger.py / test_biometric_registry.py
        # / test_auth_service.py.
        cls._patches = [
            patch("app.core.supabase.get_supabase_client", return_value=cls.fake_supabase),
            patch("app.api.routes.get_supabase_client", return_value=cls.fake_supabase),
            patch("app.observability.audit_ledger.get_supabase_client", return_value=cls.fake_supabase),
            patch("app.services.biometric_registry.get_supabase_client", return_value=cls.fake_supabase),
            patch("app.core.redis.get_redis_client", return_value=cls.fake_redis),
            patch("app.services.auth_service.get_redis_client", return_value=cls.fake_redis),
            patch("app.services.crypto_engine.get_redis_client", return_value=cls.fake_redis),
            patch("app.services.provider_auth_service.get_redis_client", return_value=cls.fake_redis),
            patch("app.services.consent_engine.get_consent_redis_client", return_value=cls.fake_async_redis),
            patch("app.main.get_redis_client", return_value=cls.fake_redis),
            patch("app.main.get_async_engine", return_value=FakeAsyncEngine()),
        ]
        for p in cls._patches:
            p.start()

        app.dependency_overrides[get_provider_context] = _override_provider_context
        app.dependency_overrides[get_db_session] = _mock_db_session
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        app.dependency_overrides.pop(get_provider_context, None)
        for p in cls._patches:
            p.stop()

    # ── Health ───────────────────────────────────────────────────────────

    def test_healthz_liveness_check(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_healthz_does_not_call_dependency_health_checks(self):
        with patch("app.main.get_redis_client") as redis_check, patch("app.main.get_async_engine") as postgres_check:
            response = self.client.get("/healthz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        redis_check.assert_not_called()
        postgres_check.assert_not_called()

    def test_health_check(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["redis"], "ok")
        self.assertEqual(data["postgres"], "ok")

    # ── Lane A: provider auth (register / enroll-biometric) ─────────────

    def test_register_without_provider_token_is_rejected(self):
        app.dependency_overrides.pop(get_provider_context, None)
        try:
            payload = {
                "patient_name": "Unauthorized Attempt",
                "phone": "5550100",
                "aadhaar_abha_id": "0000-0000-0000",
                "diagnoses": [],
                "lab_results": [],
                "prescriptions": [],
            }
            with patch("app.core.dependencies.get_db_session", side_effect=_mock_db_session):
                response = self.client.post("/register", json=payload)
            self.assertEqual(response.status_code, 401)
        finally:
            app.dependency_overrides[get_provider_context] = _override_provider_context

    def test_register_with_wrong_provider_token_is_rejected(self):
        app.dependency_overrides.pop(get_provider_context, None)
        try:
            payload = {
                "patient_name": "Wrong Key Attempt",
                "phone": "5550101",
                "aadhaar_abha_id": "0000-0000-0001",
                "diagnoses": [],
                "lab_results": [],
                "prescriptions": [],
            }
            with patch(
                "app.core.dependencies.authenticate_provider_session",
                new_callable=AsyncMock,
                return_value=ProviderAuthResult(None, ProviderAuthFailure.INVALID_CREDENTIALS),
            ), patch("app.core.dependencies.get_db_session", side_effect=_mock_db_session):
                response = self.client.post(
                    "/register",
                    json=payload,
                    headers={"Authorization": "Bearer not-the-right-key"},
                )
            self.assertEqual(response.status_code, 401)
        finally:
            app.dependency_overrides[get_provider_context] = _override_provider_context

    # ── Lane B: full patient lifecycle ──────────────────────────────────

    @patch.dict(os.environ, {"KEK_ROOT_SECRET": "test-kek-root-secret"})
    @patch("app.api.routes.decrypt_pii_field", return_value=None)
    @patch("app.services.sharding.get_encryption_provider")
    @patch("app.api.routes.get_encryption_provider")
    def test_full_patient_lifecycle(self, mock_get_route_kms, mock_get_sharding_kms, mock_decrypt_pii):
        mock_get_route_kms.return_value = FakeKMSProvider()
        mock_get_sharding_kms.return_value = FakeKMSProvider()
        # 1. Register (provider-gated)
        register_payload = {
            "patient_name": "Test Patient Lifecycle",
            "phone": "9999999999",
            "aadhaar_abha_id": "1234-5678-9012",
            "diagnoses": ["Type 2 Diabetes"],
            "lab_results": ["HbA1c: 7.2%"],
            "prescriptions": ["Metformin 500mg"],
        }
        reg_response = self.client.post(
            "/register", json=register_payload, headers=PROVIDER_HEADERS
        )
        self.assertEqual(reg_response.status_code, 200, reg_response.text)
        reg_data = reg_response.json()
        masked_id = reg_data["pii_vault"]["masked_internal_id"]
        self.assertTrue(masked_id)

        # 2. Enroll the biometric binding (provider-gated) -- the action
        # that decides which (nfc_uid, bio_seed) pair this patient trusts.
        enroll_payload = {
            "masked_internal_id": masked_id,
            "nfc_uid": "NFC-LIFECYCLE-001",
            "bio_seed": "lifecycle-bio-seed",
        }
        enroll_response = self.client.post(
            "/api/v1/enroll-biometric", json=enroll_payload, headers=PROVIDER_HEADERS
        )
        self.assertEqual(enroll_response.status_code, 201, enroll_response.text)
        self.assertEqual(enroll_response.json()["status"], "enrolled")

        # 3. Handshake: verifies the now-enrolled pair, mints a
        # patient-scoped session token.
        handshake_payload = {
            "nfc_uid": "NFC-LIFECYCLE-001",
            "bio_seed": "lifecycle-bio-seed",
            "masked_internal_id": masked_id,
        }
        handshake_response = self.client.post("/api/v1/handshake", json=handshake_payload)
        self.assertEqual(handshake_response.status_code, 200, handshake_response.text)
        session_token = handshake_response.json()["session_token"]
        self.assertTrue(session_token)
        session_headers = {"Authorization": f"Bearer {session_token}"}

        # 3b. An unenrolled (nfc_uid, bio_seed) pair for the same patient
        # must still be rejected -- proves F-03's verify_biometric_binding()
        # check is actually wired in, not just present in the module.
        bad_handshake = self.client.post(
            "/api/v1/handshake",
            json={
                "nfc_uid": "NFC-NEVER-ENROLLED",
                "bio_seed": "whatever",
                "masked_internal_id": masked_id,
            },
        )
        self.assertEqual(bad_handshake.status_code, 401)

        # 4. The old combined /api/v1/record endpoint is deprecated
        # (410 Gone) because it joined PII and clinical shards in one response.
        record_response = self.client.get("/api/v1/record", headers=session_headers)
        self.assertEqual(record_response.status_code, 410, record_response.text)

        # 5. Request a consent token -- session-scoped, no id in the body.
        consent_response = self.client.post(
            "/request-consent", json={"duration_seconds": 300}, headers=session_headers
        )
        self.assertEqual(consent_response.status_code, 200, consent_response.text)
        consent_token = consent_response.json()["consent_token"]
        self.assertTrue(consent_token)

        # 6. View the clinical shard via the default clinical consent
        # token. It must not return PII fields or reassemble the shards.
        clinical_view_response = self.client.get(
            "/view-record/clinical", headers={"X-Consent-Token": consent_token}
        )
        self.assertEqual(clinical_view_response.status_code, 200, clinical_view_response.text)
        clinical_view_data = clinical_view_response.json()
        self.assertEqual(clinical_view_data["masked_internal_id"], masked_id)
        self.assertIn("Type 2 Diabetes", clinical_view_data["diagnoses"])
        self.assertNotIn("patient_name", clinical_view_data)
        self.assertNotIn("phone", clinical_view_data)

        # 7. A clinical-only consent token must not authorize the PII
        # endpoint. Under-scoped and invalid tokens intentionally share
        # the same response shape.
        under_scoped_pii_response = self.client.get(
            "/view-record/pii", headers={"X-Consent-Token": consent_token}
        )
        self.assertEqual(under_scoped_pii_response.status_code, 403)

        # 8. A full-scope token can view the PII shard, but PII fields
        # still come back redacted (F-10), and clinical fields are absent.
        full_consent_response = self.client.post(
            "/request-consent",
            json={"duration_seconds": 300, "scope": "full"},
            headers=session_headers,
        )
        self.assertEqual(full_consent_response.status_code, 200, full_consent_response.text)
        full_consent_token = full_consent_response.json()["consent_token"]

        pii_view_response = self.client.get(
            "/view-record/pii", headers={"X-Consent-Token": full_consent_token}
        )
        self.assertEqual(pii_view_response.status_code, 200, pii_view_response.text)
        pii_view_data = pii_view_response.json()
        self.assertEqual(pii_view_data["masked_internal_id"], masked_id)
        self.assertEqual(pii_view_data["patient_name"], "[REDACTED]")
        self.assertEqual(pii_view_data["phone"], "[REDACTED]")
        self.assertNotIn("diagnoses", pii_view_data)

    # ── Negative cases for the patient lane ─────────────────────────────

    def test_request_consent_without_session_is_rejected(self):
        response = self.client.post("/request-consent", json={})
        self.assertEqual(response.status_code, 401)

    def test_view_record_without_consent_token_is_rejected(self):
        response = self.client.get("/view-record/clinical")
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()