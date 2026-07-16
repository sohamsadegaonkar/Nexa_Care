# Nexa Care Trusted Host and Device Timestamp Fix

> Historical alpha validation evidence; use current environment documentation for deployment.

## 1. Executive verdict

- Trusted-host result: Fixed without weakening production validation.
- Corrective migration result: Applied and verified.
- Final Alembic head: `20260713_device_key_timestamps`
- Test result: 76 passed, 0 failed.
- Remaining blockers: no active physical device is enrolled for the demo patient.

## 2. Trusted-host root cause

`TrustedHostMiddleware` reads `TRUSTED_HOSTS` when `app.main` is imported. Starlette/FastAPI `TestClient` uses the synthetic hostname `testserver` unless given another base URL. The local configuration allowed loopback hosts but not `testserver`, so middleware returned HTTP 400 before `/health` or consent routes executed. Module caching then preserved that import-time middleware configuration for the test process.

## 3. Trusted-host fix

- `.env.example` and the alpha runbook now document `TRUSTED_HOSTS=localhost,127.0.0.1,testserver` for local alpha/test use.
- Test setup sets the test-only value before importing `app.main`.
- Consent preflight uses an explicit `localhost` TestClient base URL.
- Application production defaults and middleware remain unchanged; untrusted hosts still return HTTP 400.
- Deployed documentation remains restricted to deployment domains.
- Physical-phone testing may require adding the current laptop LAN IP only to ignored local configuration; LAN IPs must not be committed.

Runtime presence check (values not displayed beyond membership):

- `localhost`: present
- `127.0.0.1`: present
- `testserver`: absent

No local environment file was overwritten. The documented safe local value remains `TRUSTED_HOSTS=localhost,127.0.0.1,testserver`.

## 4. Timestamp compatibility issue

`PatientDeviceKey` inherits `TimestampMixin`, which requires timezone-aware, non-null `created_at` and `updated_at` columns with database `now()` defaults. The older device-key revision did not create them. Editing that already-applied revision would help only future databases and would not repair existing revision state, so a new corrective migration was required.

## 5. Corrective migration

- Revision ID: `20260713_device_key_timestamps`
- Down revision: `20260712_tombstone_integrity`
- Upgrade: inspects `patient_device_keys`, adding each missing timestamp independently with `DateTime(timezone=True)`, `now()`, and `nullable=False`.
- Downgrade: intentionally non-destructive because the migration cannot determine whether columns predated the revision or contain real audit metadata.
- Fresh databases: safe when the columns already exist because both additions are guarded.
- Existing databases: missing columns are added and existing rows receive the database default.
- Idempotency: Alembic runs the revision once; column inspection also protects mixed legacy states.

## 6. Alembic graph

### Before

```text
20260712_tombstone_integrity (head)
```

### After

```text
20260712_tombstone_integrity
  -> 20260713_device_key_timestamps (head)
```

## 7. Schema verification

| Column | Exists | Type | Nullable | Default |
| ------ | -----: | ---- | -------: | ------- |
| `created_at` | Yes | timestamp with time zone | No | `now()` |
| `updated_at` | Yes | timestamp with time zone | No | `now()` |

## 8. Test results

| Check | Passed | Failed | Notes |
| ----- | -----: | -----: | ----- |
| Environment connectivity | 3 | 0 | PostgreSQL, Redis, Supabase passed |
| Consent preflight | 3 | 1 | Health passed; no enrolled physical device |
| Migration tests | 6 | 0 | Single head and guarded correction verified |
| Trusted-host tests | 4 | 0 | Allowed/rejected hosts and parsing verified |
| Architecture tests | 8 | 0 | Passed |
| Alpha invariant tests | 23 | 0 | Passed |
| Consent/device security tests | 14 | 0 | Passed |
| Patient-record/access-history tests | 21 | 0 | Passed |
| Python compilation | 2 | 0 | Both requested scripts compiled |
| `git diff --check` | 1 | 0 | Passed |

Pytest total: **76 passed, 0 failed**.

## 9. Files changed

- `.env.example`: local/test trusted-host example.
- `docs/alpha-environment-setup.md`: host-purpose and LAN guidance.
- `scripts/consent_preflight.py`: explicit allowed local TestClient hostname.
- `tests/conftest.py`: test-only host configuration before app import.
- `tests/test_trusted_hosts.py`: trusted/untrusted host regression coverage.
- `alembic/versions/20260713_add_patient_device_key_timestamps.py`: corrective revision.
- `tests/test_migration_graph.py`: updated head and corrective migration tests.
- Prior Phase 1 migration repair files remain modified as documented in `migration-graph-fix.md`.
- This report documents Phase 2 validation.

No application business logic or production trusted-host default changed. No LAN IP or secret was added.

## 10. Remaining unexecuted items

- Patient seeding
- Doctor seeding
- Physical mobile build
- LAN access from a physical phone
- Physical device enrollment
- Biometric approval
- Full consent loop

## 11. Exact next commands

```powershell
Set-Location C:\path\to\Nexa_Care
.\venv\Scripts\Activate.ps1
python scripts\seed_demo_patient.py
python scripts\seed_demo_doctor.py
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

In another terminal, after physical-device enrollment:

```powershell
Set-Location C:\path\to\Nexa_Care
.\venv\Scripts\Activate.ps1
$env:PYTHONUTF8='1'
python scripts\consent_preflight.py
```

Before LAN testing, add the current laptop LAN IP only to the ignored local `TRUSTED_HOSTS` value and configure the mobile API URL locally; do not commit either value.

## 12. Final recommendation

APPROVE FOR SEEDING AND BACKEND STARTUP

## 13. Git status

- Commit performed: No
- Push performed: No
