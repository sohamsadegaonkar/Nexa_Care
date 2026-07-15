# Nexa Care Phase 2 Setup Review

## 1. Executive verdict

- Overall result: PASS for configuration tooling and offline validation; external infrastructure remains unverified.
- Security verdict: PASS after review fixes. No tracked environment file or potential secret was detected in the reviewed change set.
- Windows compatibility: PASS. PowerShell parsing/help and Windows test execution succeeded; the locale-dependent UTF-8 scanner defect was fixed.
- Configuration readiness: READY BUT UNVERIFIED. The contract and validators are consistent, but the runtime `.env` is not fully configured.
- Infrastructure readiness: READY BUT UNVERIFIED. Safe PostgreSQL, Redis, and Supabase probes exist but were not executed.
- Mobile readiness: READY BUT UNVERIFIED. Source and LAN instructions are aligned; no phone-to-backend test was run.
- Physical-device readiness: READY BUT UNVERIFIED. The code path exists; no hardware test was run.
- Full-loop readiness: BLOCKED pending credentials, external connectivity, backend health, test-data seeding, LAN reachability, and physical-device execution.

## 2. Repository state

- Branch: `alpha-loop-testing`
- Base commit: `b3abf7e1902ff75fcfeb180feb99caa5651557c6`
- Working-tree status: intended Phase 2 changes plus the review portability fix in `tests/test_architecture.py`; no unrelated change found.
- Files reviewed: all ten requested Phase 2 files, runtime configuration modules, relevant mobile enrollment files, and the architecture scanner.
- Unrelated changes: none.
- Secret files tracked: none; `git ls-files .env .env.alpha` returned no paths.
- Line-ending configuration: `core.autocrlf=true`; no `.gitattributes` exists. Git reports expected LF-to-CRLF conversion warnings, while `git diff --check` passes.
- UTF-8: all reviewed files decode as strict UTF-8; no mojibake marker was found in the newly created environment files.

## 3. Environment-variable contract

| Variable | Required | Canonical source | Default | Documented | Notes |
|---|---:|---|---|---:|---|
| `SUPABASE_URL` | Yes | `app/core/config.py` | None | Yes | HTTPS project URL. |
| `SUPABASE_KEY` | Yes | `app/core/config.py` | None | Yes | Backend service-role key; no legacy alias. |
| `DATABASE_URL` | Yes | `app/core/config.py` | None | Yes | Must use `postgresql+asyncpg`. |
| `UPSTASH_REDIS_URL` | Yes | `app/core/config.py` | None | Yes | Canonical Redis name; hosted service must use `rediss`. |
| `HANDSHAKE_PEPPER_SECRET` | Yes | `app/core/config.py` | None | Yes | Canonical pepper name; no `PEPPER_SECRET` alias. |
| `KEK_ROOT_SECRET` | Yes | `app/core/config.py` | None | Yes | Required by the local envelope-encryption backend. |
| `MFA_ENCRYPTION_KEY` | Yes for full loop | `app/core/security.py` | None | Yes | Direct getter; valid Fernet key required when MFA secrets are used. |
| `PII_ENCRYPTION_KEY` | Yes for full loop | `app/core/security.py` | None | Yes | Direct getter; valid Fernet key required for PII encryption. |
| `CLINIC_API_KEY` | Conditional/deprecated | `app/core/config.py` | None | Yes | Script-only legacy getter; not provider-route authentication. |
| `DEMO_PROVIDER_PASSWORD` | Seeder only | `scripts/seed_demo_doctor.py` | None | Yes | Required by doctor seeding and never printed. |
| `ENV` | No | `app/api/v2/policy_routes.py` | `production` | Yes | Canonical environment selector; alpha template sets `alpha`. |
| `ENVIRONMENT` | Legacy alias | Reviewed seed/enrollment scripts | `development` in those scripts | Yes | Used only when `ENV` is absent. Not a runtime-policy alias. |
| `ENCRYPTION_BACKEND` | No | `app/core/config.py` | `local` | Yes | Optional `local`/KMS selector. |
| `DATABASE_ECHO_SQL` | No | `app/core/config.py` | `false` | Yes | Boolean parser accepts common true values. |
| `MAX_UPLOAD_BYTES` | No | `app/main.py` | `20971520` | Yes | Direct use outside centralized config. |
| `PUSH_STATUS_TRANSPORT` | No | `app/api/v2/assurance_routes.py` | `poll` | Yes | Direct use outside centralized config. |
| `DOCUMENT_AI_API_KEY` | No | `app/ai/extractor.py` | Blank/mock | Yes | May intentionally remain blank. |
| `DOCUMENT_AI_API_URL` | No | `app/ai/extractor.py` | Blank | Yes | May intentionally remain blank. |
| `CORS_ALLOWED_ORIGINS` | No | `app/main.py` | Empty | Yes | Comma-separated. |
| `TRUSTED_HOSTS` | No | `app/main.py` | `*` | Yes | Development default; restrict for deployed environments. |

`REDIS_URL`, `JWT_SECRET`, and `SUPABASE_SERVICE_ROLE_KEY` are not runtime names and were not introduced. The Expo push endpoint is currently a code constant, not an environment variable.

## 4. File-by-file review

### `.env.example`

- Findings: strict UTF-8, placeholders only, complete full-loop secret set, async PostgreSQL URL, TLS Redis example, independent secret-generation instructions, alpha defaults, and intentionally blank Document AI settings.
- Fixes: added script-only `DEMO_PROVIDER_PASSWORD` and optional `MAX_UPLOAD_BYTES`; retained explicit `.env.alpha` to `.env` workflow.
- Remaining concerns: all generated placeholders must be replaced independently before validation can pass.

### `.gitignore`

- Findings: `.env` and `.env.alpha` were ignored, but the original backup rule did not match the requested `.env.*.backup` convention and local/validation artifacts were not covered.
- Fixes: added `.env.*.backup`, retained compatibility for `.env.backup-*`, and added `*.local`, `alpha-test-credentials.local.txt`, and `environment-validation*.tmp`.
- Remaining concerns: none; `.env.example` remains trackable.

### `scripts/setup_alpha_environment.ps1`

- Findings: root/branch/dirty-tree/ignore checks were safe, but copying occurred before validation and `-ValidateOnly -CopyToDotEnv` could write despite its name.
- Fixes: made switches mutually exclusive, moved validation ahead of any copy, changed backups to `.env.<timestamp>.backup`, and added comment-based help.
- Remaining concerns: local Windows execution policy blocks direct script invocation on this workstation; `powershell -ExecutionPolicy Bypass -File ...` was used for review. The repository script itself parses correctly.

### `scripts/check_alpha_environment.py`

- Findings: it uses application getters, engine, and Redis client; validates Fernet/URL schemes/placeholders; uses timeouts; and closes resources. Raw exception text was unnecessarily risky, and Redis performed a temporary write.
- Fixes: exception output now reports only a category and exception type, Redis now performs only `PING`, and Redis socket/connect timeouts are set. PostgreSQL remains `SELECT 1`; Supabase uses only `/auth/v1/health` without guessing a table.
- Remaining concerns: connectivity behavior is unverified without complete ignored runtime credentials.

### Seed and enrollment scripts

- Findings: the removed doctor-seeder `PatientDeviceKey` construction used obsolete fields and fake cryptographic identity material. Patient and enrollment scripts correctly prefer canonical `ENV` with a legacy fallback. The doctor seeder lacked an equivalent production guard and printed a password/session token.
- Fixes: fake device enrollment remains removed; doctor seeding now refuses `prod`/`production`, obtains its password from `DEMO_PROVIDER_PASSWORD`, and no longer issues or prints a bearer token or password. No seeder was executed.
- Remaining concerns: seeders intentionally mutate the selected alpha database and must be run only after target verification.

### Documentation

- Findings: the alpha runbook and blank evidence template accurately separate code readiness from physical proof. Demo documentation used `ENVIRONMENT` instead of canonical `ENV` and described `EXPO_PUSH_API_URL` as configurable although it is a code constant.
- Fixes: aligned `ENV`, corrected Expo endpoint guidance, documented `MAX_UPLOAD_BYTES`, conditional demo password, read-only Redis validation, normal `.env` loading, LAN binding, firewall scope, and physical limitations.
- Remaining concerns: actual infrastructure/device evidence remains blank by design.

## 5. Security review

- Secret exposure: no potential secret was found by the redacted pattern scan; environment files were never read or printed directly.
- Placeholder handling: required values reject blanks and known placeholder forms; blank optional Document AI values are permitted.
- URL redaction: connectivity errors never print URLs or raw exception details.
- Exception sanitization: external checks return exception type plus DNS/TLS/authentication/timeout/driver/general category only.
- Environment-file safety: no tracked `.env`; `.env`, `.env.alpha`, timestamped backups, local credential files, and validation temporary files are ignored.
- Production safeguards: all three reviewed seed/enrollment scripts reject `prod` and `production`, preferring canonical `ENV`.
- Database mutation risk: configuration checks use only `SELECT 1`; seeders were not run.
- Redis mutation risk: the checker now uses only `PING` and performs no key write.

## 6. Mobile and device-enrollment readiness

- API URL configuration: canonical source variable is `NEXT_PUBLIC_API_URL` in the shared API client.
- LAN access: documented with a placeholder `http://<LOCAL_LAN_IP>:8000`; no developer-specific IP is committed.
- Backend binding: runbook uses Uvicorn `--host 0.0.0.0`.
- UI enrollment path: Expo route `/patient/secure-device` renders `SecureDeviceScreen`, which calls `generateAndEnrollDevice`.
- Secure key storage: private P-256 bytes remain in Expo SecureStore with device-only accessibility; alpha documentation accurately states this is exportable JS key material, not hardware-backed non-exportable storage.
- Public-key submission: only DER/base64 public key plus device metadata is sent.
- P-256 enforcement: mobile uses `@noble/curves/p256`; backend requires `SECP256R1`.
- Authentication binding: backend obtains patient identity from `get_scoped_session`, not request JSON.
- Device limit: backend enforces five active devices per patient.
- Physical verification status: NOT RUN. No source claims physical proof.

## 7. Test results

| Test or check | Result | Passed | Failed | Notes |
|---|---|---:|---:|---|
| Python compilation | PASS | 4 files | 0 | Checker and three reviewed seed/enrollment scripts. |
| PowerShell parsing | PASS | 1 | 0 | PowerShell language parser returned no errors. |
| PowerShell help | PASS | 1 | 0 | Verified in a bypassed child PowerShell due workstation execution policy. |
| PowerShell `-ValidateOnly` | Expected nonzero | 0 | 1 | Safely reported configured booleans only; required values remain missing/placeholders. |
| `git diff --check` | PASS | 1 | 0 | CRLF conversion warnings are non-failing with `core.autocrlf=true`. |
| Environment validator compilation/help | PASS | 2 | 0 | CLI exposes all requested switches. |
| Environment validator config | Expected nonzero | 0 | 1 | Runtime `.env` is incomplete; output contained names/booleans/errors only. |
| Alpha invariants | PASS | 23 | 0 | Exact requested suite. |
| Device consent/security | PASS | 14 | 0 | Exact requested suite. |
| Patient records/access history | PASS | 21 | 0 | Exact requested suite. |
| Architecture/device tests | PASS | 14 | 0 | Includes six repeated device tests. |
| Encoding failure before fix | FAIL then fixed | 0 | 2 | Windows default CP1252 could not decode UTF-8 application source. |
| Encoding rerun after fix | PASS | 14 | 0 | Explicit UTF-8 scanner read resolved both failures. |

Total executed pytest cases: 72 passed, 0 failed after fixes (66 unique tests; six device tests were intentionally repeated by the requested commands).

## 8. Issues discovered

### Issue 1

- Severity: High
- File and line: `scripts/setup_alpha_environment.ps1:29-72`
- Description: copy-to-runtime occurred before validation, and validation-only could be combined with copying.
- Impact: an invalid `.env.alpha` could replace `.env` despite eventual failure.
- Root cause: mutation block preceded validation and switches were not exclusive.
- Resolution: validate first, reject conflicting switches, then back up/copy.
- Verification: parser passed; `-ValidateOnly` exited nonzero without writing or exposing values.

### Issue 2

- Severity: High
- File and line: `scripts/seed_demo_doctor.py:43,180-216`
- Description: no production environment guard; password and generated bearer token were printed.
- Impact: accidental production seeding and credential leakage through terminal/log capture.
- Root cause: older demo-oriented script assumptions.
- Resolution: added canonical environment guard with legacy fallback, moved password to an environment variable, removed token issuance and secret output.
- Verification: compilation passed; source inspection confirms no password/token print.

### Issue 3

- Severity: Medium
- File and line: `.gitignore:11-16`, `scripts/setup_alpha_environment.ps1:68`
- Description: backup/local validation artifacts were not consistently ignored under the requested naming convention.
- Impact: credentials or validation artifacts could appear as untracked files and be staged accidentally.
- Root cause: backup filename and ignore rule used a different convention.
- Resolution: standardized `.env.<timestamp>.backup` and added narrow ignore rules.
- Verification: `git check-ignore -v` passed for every requested artifact; `.env.example` remains unignored.

### Issue 4

- Severity: Medium
- File and line: `scripts/check_alpha_environment.py:28-48,104-120`
- Description: raw exception fragments could leak connection details and Redis validation mutated a temporary key.
- Impact: avoidable secret leakage or infrastructure mutation during review validation.
- Root cause: overly detailed error reporting and an unnecessary write test.
- Resolution: categorical redaction and read-only `PING` with explicit timeouts.
- Verification: compilation passed; config failure output was redacted; source contains no Redis write.

### Issue 5

- Severity: Medium
- File and line: `tests/test_architecture.py:38`
- Description: repository scanner used locale-default decoding on Windows.
- Impact: two architecture tests failed before analyzing source.
- Root cause: `Path.read_text()` omitted `encoding="utf-8"`.
- Resolution: added explicit UTF-8 decoding.
- Verification: architecture/device suite passed 14/14.

### Issue 6

- Severity: Low
- File and line: `docs/demo-env-config.md:15,34-36`; `.env.example:25-32`
- Description: documentation used a noncanonical environment name, treated a constant as an environment variable, and omitted `MAX_UPLOAD_BYTES` from the template.
- Impact: deployment/configuration drift and misleading operator instructions.
- Root cause: stale demo documentation.
- Resolution: documented canonical `ENV`, described the Expo endpoint as a constant, and added the upload setting.
- Verification: targeted source search matches the updated contract.

## 9. Changes made during review

- `.env.example`: added conditional demo password and optional upload-size configuration.
- `.gitignore`: added safe backup, local credential, and temporary validation patterns.
- `docs/demo-env-config.md`: corrected `ENV` and Expo endpoint semantics.
- `docs/alpha-environment-setup.md`: aligned activation path, contract, read-only probes, and script-only password guidance.
- `scripts/setup_alpha_environment.ps1`: made validation precede copying, made switches exclusive, standardized backups, and added help.
- `scripts/check_alpha_environment.py`: removed Redis mutation, strengthened timeouts, and fully redacted exception detail.
- `scripts/seed_demo_doctor.py`: added production refusal, removed hardcoded/printed password and bearer-token output.
- `tests/test_architecture.py`: made repository source scanning explicitly UTF-8.
- `docs/alpha-validation/phase-2-setup-review.md`: created this review report.

No consent business rule, authentication design, cryptographic protocol, migration, or production infrastructure was changed.

## 10. Items not executed

- PostgreSQL connectivity: NOT RUN because complete real ignored runtime credentials were unavailable; no `SELECT 1` result is claimed.
- Redis connectivity: NOT RUN because complete real ignored runtime credentials were unavailable; no `PING` result is claimed.
- Supabase connectivity: NOT RUN because complete real ignored runtime credentials were unavailable; no health result is claimed.
- Backend startup: NOT RUN because required runtime configuration failed validation.
- Health endpoint: NOT RUN because the backend was not started.
- Seeding: NOT RUN by explicit review restriction; seeders mutate the configured database.
- Mobile LAN: NOT RUN because no backend/device LAN session was started.
- Physical device: NOT RUN because no physical hardware session was performed.
- Real biometric enrollment: NOT RUN; code readiness only.
- Full consent loop: NOT RUN; infrastructure, data, LAN, and hardware prerequisites remain outstanding.

## 11. Readiness matrix

| Area | Status | Blocking issue |
|---|---|---|
| Configuration tooling | PASS | None |
| PostgreSQL validation | READY BUT UNVERIFIED | Complete ignored runtime configuration and live execution required |
| Redis validation | READY BUT UNVERIFIED | Complete ignored runtime configuration and live execution required |
| Supabase validation | READY BUT UNVERIFIED | Complete ignored runtime configuration and live execution required |
| Backend startup | BLOCKED | Configuration validation does not yet pass |
| Test-data seeding | READY BUT UNVERIFIED | Target verification and explicit execution required |
| Mobile LAN access | READY BUT UNVERIFIED | Running backend, firewall/LAN, and physical phone required |
| Device enrollment code | PASS | Offline code/test readiness only |
| Physical device enrollment | NOT RUN | Physical development build and biometrics required |
| Full consent loop | BLOCKED | All external and physical gates above remain incomplete |

## 12. Exact next commands

Run these from a trusted PowerShell session. Enter secrets only by editing ignored files or through a secret manager; do not paste them into shared logs.

```powershell
Set-Location C:\Users\DELL\Nexa_Care
.\venv\Scripts\Activate.ps1

powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_alpha_environment.ps1 -ValidateOnly
# Complete the existing ignored .env.alpha without printing it.
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_alpha_environment.ps1 -CopyToDotEnv

python scripts\check_alpha_environment.py --config-only
python scripts\check_alpha_environment.py --all

# Only after confirming ENV=alpha and the target database is disposable alpha data:
python scripts\seed_demo_patient.py
python scripts\seed_demo_doctor.py
python scripts\consent_preflight.py

python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
Invoke-RestMethod http://127.0.0.1:8000/healthz
Invoke-RestMethod http://127.0.0.1:8000/health
```

In a separate terminal, replace the example with the workstation's current private-LAN address without committing it:

```powershell
Set-Location C:\Users\DELL\Nexa_Care\nexa-client
$env:NEXT_PUBLIC_API_URL='http://<LOCAL_LAN_IP>:8000'
yarn workspace expo start --dev-client
```

## 13. Final recommendation

**APPROVE FOR PHASE 2 INFRASTRUCTURE EXECUTION**

The reviewed configuration tooling is secure, repeatable, Windows-compatible, read-only for connectivity checks, and consistent with the runtime contract after the fixes above. Approval covers the next infrastructure-execution stage only. It is not evidence that PostgreSQL, Redis, Supabase, backend health, LAN access, physical enrollment, biometrics, or the full consent loop passed.

## 14. Git summary

- Modified files: `.env.example`, `.gitignore`, `docs/demo-env-config.md`, `scripts/enroll_demo_device.py`, `scripts/seed_demo_doctor.py`, `scripts/seed_demo_patient.py`, `tests/test_architecture.py`
- Created files: `docs/alpha-environment-setup.md`, `docs/alpha-validation/environment.md`, `docs/alpha-validation/phase-2-setup-review.md`, `scripts/check_alpha_environment.py`, `scripts/setup_alpha_environment.ps1`
- Deleted files: none
- Diff summary: Phase 2 environment template/tooling/docs, safe seed-script corrections, and one UTF-8 scanner portability fix. Tracked diff before this untracked report contained 53 insertions and 77 deletions across seven tracked files; untracked files are not included by `git diff --stat`.
- Commit performed: No
- Push performed: No
