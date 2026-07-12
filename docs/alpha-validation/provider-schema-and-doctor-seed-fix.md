# Nexa Care Provider Schema and Doctor Seed Fix

## 1. Executive verdict

- Root cause: Provider/NFC models existed, but their legacy SQL definitions were never represented in the Alembic chain, leaving five runtime tables absent.
- Corrective migration: `20260714_provider_schema`
- Final Alembic head: `20260714_provider_schema`
- Doctor seed: Passed.
- Seeder idempotency: Passed for patient and doctor seeders on two consecutive runs.
- Test status: 82 passed, 0 failed.
- Remaining blockers: physical-device enrollment and the subsequent biometric/full-consent loop.

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

- High — all five provider/NFC tables were absent. Runtime impact: doctor seed and provider authentication could not begin. Correction: guarded Alembic table creation.
- High — provider foreign-key graph was absent. Correction: hospital and provider targets are created before affiliation/credential references.
- Medium — model indexes, uniqueness, NFC status check, and inherited timestamps had no Alembic representation. Correction: exact modeled objects were included.
- Medium — doctor clinical seed SQL reused an untyped bind across varchar comparison/insertion and passed untyped lists to JSONB. Runtime impact: asyncpg ambiguity/encoding failures after schema repair. Correction: explicit `String(64)` and `JSONB` bind types; seed content and business behavior are unchanged.

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

## 8. Test results

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

## 10. Remaining blockers

Provider schema blockers: none. Doctor seed blockers: none. Expected physical workflow blocker: no active physical device is enrolled, so biometric approval and the complete consent loop remain unexecuted.

## 11. Exact next commands

```powershell
Set-Location C:\Users\DELL\Nexa_Care
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
