# Nexa Care Provider Schema and Doctor Seed Fix

> Historical alpha validation evidence; demo seeding is not production onboarding.

## 1. Executive verdict (updated 2026-07-15)

- Original schema root cause: provider/NFC models existed without Alembic coverage.
- Provider-login follow-up: the canonical schema and seeder used `password_hash`, while authentication preferred the transitional `hashed_password` column whenever populated. The live alpha legacy column was empty, so this ambiguity did not cause the observed 401, but it was unsafe and has been removed.
- Observed 401 root cause: the live canonical hash matched the ignored local environment password; the account and affiliation were active. Four failed attempts and obsolete fixed-password documentation indicate incorrect login input was used. A safe pre-rotation HTTP check returned 200.
- Canonical password field: `provider_credential.password_hash` only.
- Corrective password migration: `20260717_provider_pwd_canonical`.
- Final Alembic head: `20260717_provider_pwd_canonical`.
- Normal doctor seed: two live runs reused all rows and reported `password=unchanged`.
- Explicit password rotation: completed for only `demo.doctor@nexacare.in`; failed attempts and lockout were cleared, prior sessions were revoked, and a security audit event was written.
- Real post-rotation login: HTTP 200 with token/provider/hospital fields present and MFA not enabled.
- Focused provider/auth/migration tests: 69 passed. Broader selected authentication suite: 86 passed.

## 2. Observed failure

The doctor seeder failed safely with PostgreSQL `UndefinedTableError: relation "hospital_registry" does not exist`. No connection strings or credentials were logged.

## 3. Provider model inventory

| Model | Table | Required by | Present before | Migration coverage before |
|---|---|---|---:|---:|
| `HospitalRegistry` | `hospital_registry` | hospital seed, affiliation/auth context | No | No Alembic coverage |
| `ProviderIdentity` | `provider_identity` | doctor seed and provider authentication | No | No Alembic coverage |
| `ProviderHospitalAffiliation` | `provider_hospital_affiliation` | hospital affiliation and login context | No | No Alembic coverage |
| `ProviderCredential` | `provider_credential` | provider login, password and MFA | No | No Alembic coverage |
| `NFCCardRegistry` | `nfc_card_registry` | NFC seed and card resolution | No | No Alembic coverage |

Seed dependency order is hospital, provider identity, credential/affiliation, NFC card, then clinical shard rows. The models use string columns for affiliation/card states; no custom PostgreSQL enum was required.

## 4. Schema gaps discovered

- High â€” all five provider/NFC tables were absent. Runtime impact: doctor seed and provider authentication could not begin. Correction: guarded Alembic table creation.
- High â€” provider foreign-key graph was absent. Correction: hospital and provider targets are created before affiliation/credential references.
- Medium â€” model indexes, uniqueness, NFC status check, and inherited timestamps had no Alembic representation. Correction: exact modeled objects were included.
- Medium â€” doctor clinical seed SQL reused an untyped bind across varchar comparison/insertion and passed untyped lists to JSONB. Runtime impact: asyncpg ambiguity/encoding failures after schema repair. Correction: explicit `String(64)` and `JSONB` bind types; seed content and business behavior are unchanged.

## 5. Corrective migration

- Revision ID: `20260714_provider_schema`
- Down revision: `20260713_device_key_timestamps`
- Tables created: `hospital_registry`, `provider_identity`, `provider_hospital_affiliation`, `provider_credential`, `nfc_card_registry`
- Enums created: none; current models use constrained strings.
- Indexes: every explicit model index was created.
- Constraints: primary keys, model unique constraints, provider foreign keys, and NFC status check.
- Dependency order: hospital; provider; affiliation; credential; NFC.
- Downgrade policy: intentionally forward-only/non-destructive because provider identities, credential hashes, affiliations, and NFC bindings may contain real data or may originate from legacy SQL deployments.

Each table is guarded with SQLAlchemy inspection, allowing an existing database at the prior head to upgrade without recreating present tables.

## 6. Schema verification

| Object | Exists | Matches model | Notes |
|---|---:|---:|---|
| `hospital_registry` | Yes | Yes | timestamps, uniqueness, indexes verified |
| `provider_identity` | Yes | Yes | FK, timestamps, uniqueness, indexes verified |
| `provider_hospital_affiliation` | Yes | Yes | two FKs, compound uniqueness, indexes verified |
| `provider_credential` | Yes | Yes | FK, credential columns, uniqueness, indexes verified |
| `nfc_card_registry` | Yes | Yes | status check, timestamps, uniqueness, indexes verified |

## 7. Seeder results

- First patient run: passed.
- Second patient run: passed; canonical patient reused.
- First doctor run: passed after schema migration and typed-bind correction.
- Second doctor run: passed; same hospital/provider reused.
- Duplicate checks: exactly one demo hospital, provider, credential, affiliation, and NFC record.
- Credentials handling: provider password presence was checked as a boolean only; no password or hash was printed.

## 8. Original provider-schema test results

| Test/check | Passed | Failed | Notes |
|---|---:|---:|---|
| Environment connectivity | 3 | 0 | PostgreSQL, Redis, Supabase |
| Consent preflight | 3 | 1 | Only no active physical device |
| Migration graph | 6 | 0 | Single new head |
| Provider schema migration | 6 | 0 | Coverage, order, metadata, compatibility |
| Trusted hosts | 4 | 0 | Passed |
| Architecture | 8 | 0 | Passed |
| Alpha invariants | 23 | 0 | Passed |
| Device consent/security | 14 | 0 | Passed |
| Patient records/access history | 21 | 0 | Passed |
| Compilation | 2 | 0 | Seeder and migration |
| `git diff --check` | 1 | 0 | Passed |

Pytest total: **82 passed, 0 failed**. The preflight physical-device status is an operational readiness item, not a test failure.

## 9. Files changed

- `alembic/versions/20260714_add_provider_schema.py`: forward provider/NFC schema correction.
- `tests/test_provider_schema_migration.py`: focused provider migration coverage.
- `tests/test_migration_graph.py`: expected head updated while preserving timestamp-revision assertions.
- `scripts/seed_demo_doctor.py`: explicit SQLAlchemy bind types for varchar and JSONB parameters.
- `docs/alpha-validation/provider-schema-and-doctor-seed-fix.md`: this report.
- Earlier uncommitted Phase 1/2 migration, trusted-host, documentation, and test changes remain in the working tree and were preserved.

## 10. Current readiness

Provider schema blockers: none. Doctor seed blockers: none. Provider login is
ready to create the real consent request. This repair did not automatically
trigger a consent request.

## 11. Exact next commands

```powershell
Set-Location C:\path\to\Nexa_Care
.\venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

After enrolling the authenticated physical device:

```powershell
$env:PYTHONUTF8='1'
python scripts\consent_preflight.py
```

## 12. Final recommendation

APPROVE FOR BACKEND STARTUP

## 13. Git status

- Commit performed: No
- Push performed: No

## 14. Provider credential canonicalization details

- `password_hash` is the sole runtime/model/seeder/reset field.
- Migration upgrade resolves canonical-only, legacy-only, and equal values;
  rejects missing or conflicting values; normalizes login identifiers; creates
  case-insensitive uniqueness; and removes `hashed_password`.
- Migration downgrade restores the legacy column from the canonical value so
  older code does not receive an empty hash.
- Unknown accounts perform a dummy password verification before returning the
  same generic 401 used for incorrect passwords.
- Login trims and lowercases identifiers. Both `is_active` and provider
  `status == "active"` are enforced.
- The normal seed command never rotates an existing password. Reset requires
  both `--reset-password` and `--confirm-demo-provider-reset`; optional account
  reactivation requires separate explicit flags.
- Rotation clears password failures/lockout, updates `password_changed_at`,
  revokes provider and pending-MFA sessions, and appends a canonical audit event.
- Post-rotation verification found one canonical credential, no legacy column,
  zero failed attempts, no lockout, one audit event, no pending MFA session, and
  one bearer session created by the successful validation login.
