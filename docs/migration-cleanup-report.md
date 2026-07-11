# Nexa Care Repository Cleanup — Migration Report

**Date:** 2026-07-11  
**Scope:** Remove duplicate root-level `apps/` and `packages/` frontend directories, migrate unique patient files into `nexa-client/`, and unify on the 9-pipe signing contract.

---

## Summary of Changes

### A. Root scaffolding deleted
- **`apps/`** — deleted entirely (expo patient routes, next doctor routes, README.md)
- **`packages/`** — deleted entirely (doctor screens, patient screens, pipeline screens, services, schemas, utils)

### B. Patient files migrated from root → nexa-client (completed by prior session)
All 14 root-only `packages/app/` files and 9 root-only `apps/expo/app/patient/` route files were migrated into `nexa-client/` before deletion.

### C. 9-pipe signing contract unified (completed by prior session)
- `nexa-client/packages/app/utils/deviceKey.ts` — rewritten with `signConsentChallenge()` using 9-pipe format: `request_id|patient_id|provider_id|nonce|decision|scope|purpose|duration|expires_at`
- `signPushChallenge()` deprecated (throws error pointing to `signConsentChallenge`)
- `PatientApprovalScreen.tsx` updated to use `signConsentChallenge` with full 9 attributes
- `assurance.ts` updated with new fields: `clinician_id`, `scope`, `access_duration`, `expires_at`

### D. Import fixes (completed by prior session)
- All `@nx/app/` aliases replaced with relative imports (`../../utils/apiClient`, `../../services/consentSigning`, etc.)
- `PatientLoginScreen.tsx` — uses `setAuthTokenProvider` from `../../utils/api`
- Patient screens using `apiClient` from `../../utils/api` (axios-based)
- Doctor screens using `NexaApiClient` from `../../utils/apiClient`
- Expo route imports: `@nx/app/features/patient/` → `app/features/patient/`

### E. Test fixes (session 1)

#### Files updated with new paths:
| Test File | Changes |
|-----------|---------|
| `test_backend_contract_and_schemas.py` | Removed `TestScaffoldingMarker` class (3 tests); updated `@nx/app/schemas/authNfcSchemas` → `authNfcSchemas` |
| `test_doctor_screens.py` | Updated `@nx/app/` assertions to check for relative imports; `setJwt` → `setAuthTokenProvider`; nfcResolve path → `nexa-client/`; `from 'tamagui'` → also accept `'@my/ui'`; flexible endpoint checks for `breakGlassIssue`, `getPatientSummary`, `cancelConsentRequest` |
| `test_patient_screens.py` | Fixed double `nexa-client/nexa-client` API_CLIENT_PATH; `@nx/app/` → relative import checks; route paths → `nexa-client/apps/expo`; deep-link test uses Expo Router scheme; review route path corrected |
| `test_device_keys.py` | `@nx/app/` → `apiClient`/`deviceKeys` relative import checks |
| `test_consent_signing.py` | `@nx/app/` → `apiClient`/`consentSigning` relative import checks |
| `test_consent_security_hardening.py` | Root `packages/` → `nexa-client/` paths; cancel endpoint check accepts `cancelConsentRequest` method |
| `test_pipeline_screens.py` | Root `packages/`/`apps/` → `nexa-client/` paths; `tamagui` → also accept `@my/ui`; `apiUpload`/`uploadFile` → `uploadDocument`; flexible endpoint checks |
| `test_record_viewer_and_emergency.py` | Root `packages/` → `nexa-client/` paths; `from 'tamagui'` → also accept `@my/ui`; break-glass endpoint accepts `breakGlassIssue` method |
| `test_safety_visibility.py` | Root `packages/` → `nexa-client/` paths; `from 'tamagui'` → also accept `@my/ui`; `@nx/app/` → `apiClient`; API method/type checks now also check screen source |

#### Vitest test updated:
| File | Changes |
|------|---------|
| `PatientApprovalScreen.test.tsx` | `signPushChallenge` → `signConsentChallenge` with full 9-attribute input; added `getDeviceId` mock |

### F. Docs updated
| File | Changes |
|------|---------|
| `docs/doctor-app-flow.md` | Repository structure section updated (removed root scaffolding references, noted removal); screen reference table uses `nexa-client/` paths; removed `packages/app/schemas/authNfcSchemas.ts` scaffolding reference |
| `docs/doctor-app-demo-setup.md` | `cd apps/next` → `cd nexa-client/apps/next` |

### G. Source code fixes (session 2)

#### Missing type definitions added:
| File | Changes |
|------|---------|
| `AccessHistoryScreen.tsx` | Added `interface AccessHistoryEntry` with fields matching backend response: `audit_id`, `doctor_name`, `hospital_name`, `purpose`, `accessed_at`, `data_categories`, `is_break_glass`, `flag`, `event_type` |
| `PatientTimelineScreen.tsx` | Added `interface TimelineEntry` with fields matching backend response: `event_id`, `event_type`, `title`, `summary`, `occurred_at`, `source`, `confidence`, `risk_level`, `badges`, etc. |

#### API call fixes:
| File | Changes |
|------|---------|
| `AccessHistoryScreen.tsx` | `apiClient.getAccessHistory()` → `apiClient.get('/api/v2/patient/me/access-history')` (axios doesn't have custom methods) |
| `PatientTimelineScreen.tsx` | `apiClient.getMyTimeline()` → `apiClient.get('/api/v2/patient/me/timeline')` |

#### Additional test fixes:
| Test File | Changes |
|-----------|---------|
| `test_patient_screens.py` | `getAccessHistory`/`getMyTimeline` method checks → endpoint URL checks (`access-history`/`timeline`) |
| `test_safety_visibility.py` | Same method → endpoint URL check updates |

---

## Validation Results

### Test Suite
- **1,191 migration-affected tests:** ✅ All pass
- **1,992 total tests:** 1,992 passed, 14 failed (pre-existing backend API failures unrelated to migration)
- **14 pre-existing failures:** Pipeline review/commit integration tests, route registration, alpha invariants — all backend issues, none reference frontend paths

### Grep Checks
| Check | Result |
|-------|--------|
| Root `apps/` or `packages/` directories | ✅ Deleted |
| `@nx/app` imports in nexa-client source | ✅ None |
| `signPushChallenge` active usage | ✅ Only deprecated stub |
| `signConsentChallenge` (9-pipe) usage | ✅ Active in screens and utils |
| Hardcoded `localhost` in source | ✅ Only in comments |
| Direct `fetch()` in screens/services | ✅ None |
| Raw `axios` imports in screens/services | ✅ None |
| `AccessHistoryEntry` type definition | ✅ Defined in AccessHistoryScreen |
| `TimelineEntry` type definition | ✅ Defined in PatientTimelineScreen |
| Root `packages/`/`apps/` paths in tests | ✅ None (all point to `nexa-client/`) |
| Docs referencing root scaffolding | ✅ All updated (remaining refs are inside `nexa-client/` tree) |

---

## Files NOT Deleted (as specified)
- `app/` — Python backend
- `tests/` — Python test suite
- `scripts/` — Utility scripts
- `docs/` — Documentation
- `alembic/` — Database migrations
- `migrations/` — Database migrations
- `nexa-client/` — Canonical production frontend
- `.github/` — CI/CD workflows
- `.gitignore` — Git configuration
- `.env.example` — Environment template
- `README.md` — Project readme
- `pyproject.toml` — Python project config
- `requirements.txt` — Python dependencies
