# Nexa Care Python dependency remediation checkpoint — 21 November 2026

**Source:** [security remediation branch](https://github.com/sohamsadegaonkar/Nexa_Care/tree/security/dependency-audit-foundation-20261121), branched from `main` `6f9959d8ad48804c3bfdac3501fb480f7e2f7708`. This is a source-and-CI *candidate*, **not a verified security clearance or release approval**.

## Prioritized security observations

The public repository pins an older parser/JWT/ASGI dependency set, including `pypdf==5.9.0`, `Pillow==12.3.0`, `PyJWT==2.10.1`, `cryptography==44.0.3` and `fastapi==0.105.0` (pulling an old Starlette). A user-supplied `pip-audit` report found numerous version-matched advisories; **its precise count and reachability have not yet been independently reproduced against the resolved CI install**. The high-priority attack surfaces are untrusted document parsing and patient-session JWT validation. Do not treat a vulnerability count as proof that every finding is exploitable, or a passing unit suite as evidence that the old versions are safe.

This first compatibility slice:
- Pins `python-multipart==0.0.32` instead of the unbounded `>=0.0.9`. This version predates the most recent release and must be checked with the old FastAPI multipart import path and upload endpoints on Python 3.12.
- Replaces **yanked** `alembic==1.15.0` with **non-yanked** `alembic==1.15.2` within the same minor line. Alembic migrations have **not** been run outside disposable CI.
- Moves `pytest==8.4.2` from production to `requirements_dev.txt`. `setuptools==70.2.0` and `wheel==0.47.0` are removed from runtime requirements but remain pinned in the existing `pyproject.toml` build-system section. No runtime app logic or database schema is changed.
- Adds an on-PR/manual-only `pip-audit==2.10.1` workflow with **resolved transitive dependency** scanning. It deliberately fails if any known advisories remain; the failure must not be waived, silently ignored, or interpreted as a runtime test failure. It prints only aggregate package/advisory counts and does not publish environment secrets or Python dependency source artifacts.

## Mandatory qualification before merging

1. Confirm the new Python 3.12 dependency resolver can install the production and dev requirements without yanked/cross-package conflicts.
2. Run backend lint and every unskipped unit + PostgreSQL + Redis partition (including actual PDF/image upload and FastAPI multipart form endpoints).
3. Run the new resolved-dependency audit. Freeze report ID, matched version, advisory, affected path, fix availability and reachable parser/JWT/HTTP entry points; resolve the **security gate** before release, preferably with tested, upgrade-bounded patches.
4. Upgrade `pypdf` and `Pillow` with adversarial document parser fixtures and exact source preservation/size limits; upgrade `PyJWT` with login, expiry, algorithm-confusion, refresh, replay and patient isolation tests; upgrade FastAPI+Starlette as one API compatibility slice with upload/form/error-status/schema/browser/MFA checks; upgrade `cryptography` with patient encryption, enrolled signatures and decrypt/erasure tests.
5. Evaluate provider-wide MFA anomaly ceiling (without easy third-party account lockouts) and legacy UUID break-glass default-off control in separate security changes and route/Redis/adversarial tests.
6. Split brittle Python TSX source-grep tests into frontend Vitest behavior checks without dropping visible ALPHA clinical-truth warnings; reconcile the `main` CI regression with the active integration branch.
7. Review privacy and release exposure before publication: repository visibility is **owner decision**; changing it can disrupt collaborators/forks/deployments. Existing publicly committed threat-model material must be assumed already disclosed, even if the repo becomes private. No repo visibility or sharing setting was changed.

## Ongoing work decisions

**Dependabot:** requires a recurring GitHub update schedule and has not been added, pending explicit owner approval. **CodeQL:** should be added as a separately reviewed PR CI check after baseline build/test compatibility is verified. No timed monitoring, production deployment, clinical/PHI data access, automatic migrations, repo visibility change, or branch cleanup was performed.

**Security/governance:** SEC-001/002/003/005/011/013/016/020/023 and REG-001/003, patient isolation, consent, cryptographic provenance, truthful clinical labels and E0 identity-evidence boundaries remain unchanged. No mutation of patient clinical data, consent/access, audit, identity, AI extraction, storage, erasure or emergency logic. Independent human security/privacy/clinical review and physical-device rehearsal remain HOLD.

## Automated advisory decline and pinned candidate status

A phased upgrade was committed on [draft PR #102](https://github.com/sohamsadegaonkar/Nexa_Care/pull/102) while keeping source upload/clinical authorization unchanged:
- `PyJWT 2.10.1 → 2.15.1` (patient and provider session compatibility tests required)
- `pypdf 5.9.0 → 7.1.1` (high-risk untrusted document parsing compatibility/adversarial tests required)
- `Pillow 12.3.0 → 13.0.0` (image imports and PDF rasterization compatibility/adversarial tests required)
- `cryptography 44.0.3 → 50.0.2` (patient-DEK encryption, JWT signing, P-256 signatures and erasure tests required)
- `python-multipart >=0.0.9 → 0.0.32`, `alembic 1.15.0 (yanked) → 1.15.2`; `pytest` moved out of production requirements; build-system setuptools/wheel unchanged.

**Actual pip-audit chronological evidence**, each against the resolved production dependency tree from the exact listed commit:
| Commit | Vulnerable packages | Matched advisories | Source |
| --- | ---: | ---: | --- |
| `eea849a` initial multipart/Alembic pin | 10 | 202 | [audit 44906119569](https://github.com/sohamsadegaonkar/Nexa_Care/actions/runs/44906119569) |
| `8b96809` + PyJWT | 9 | 180 | [audit 44906288768](https://github.com/sohamsadegaonkar/Nexa_Care/actions/runs/44906288768) |
| `9eeee72` + pypdf | 8 | 77 | [audit 44906341642](https://github.com/sohamsadegaonkar/Nexa_Care/actions/runs/44906341642) |
| `6565be3` + Pillow | 7 | 35 | [audit 44906443967](https://github.com/sohamsadegaonkar/Nexa_Care/actions/runs/44906443967) |
| `7d8879e` + cryptography | 6 | 25 | [audit 44906501601](https://github.com/sohamsadegaonkar/Nexa_Care/actions/runs/44906501601) |

These are **version-based advisory matches, not individual proven exploits**, and counts can vary by advisory database. The audit intentionally reports FAILURE until the resolved dependency tree is cleared. The workflow now emits limited, non-secret per-package name/version/advisory-count summaries for prioritization. No advisories are ignored or auto-fixed.

**CI evidence boundary:** On `9eeee72` the real PostgreSQL, Redis, ClamAV and Azure workflows passed; pure unit CI failed **the same four existing main UI source-grep assertions** (ReviewQueue/ReviewCockpit JSDoc and DocumentPreview count/ALPHA) unrelated to the dependency upgrades. Those failing checks must be fixed with visible clinical-truth behavior and/or properly migrated to Vitest, not silently discarded. Later P0 package revision test suites were still in progress at this documentation checkpoint; check each exact SHA before claiming compatibility. Full live Azure and physical Android evidence remain unavailable.

**Next remediation** is a separate FastAPI/Starlette version-compatibility slice and any remaining vulnerable transitive packages, with real request/form, clinician cookie/CSRF, consent and browser/one-time-source regression qualification. CodeQL is not yet enabled. Do not merge into `main` until runtime CI, advisory gate and human security reviewer gates are resolved.
