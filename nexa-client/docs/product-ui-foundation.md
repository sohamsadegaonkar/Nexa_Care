# Nexa Care product UI foundation

Branch: `ui-ux-product-polish`  
Base and current HEAD: `902e087647c71ff673ab0df84135f8b1564bbcd8`  
Delivery: uncommitted frontend patch; no commit, push, PR, merge, or deployment performed.

The checkout initially had clean `main` at `653d1baefbe28845923a99115603f468be5c0210`, one documentation-only commit ahead of the requested baseline. The UI branch starts directly at the requested baseline; `main` was preserved.

## Implemented scope

This is the coherent first foundation batch allowed by the product brief. It covers shared design components, provider entry and workspace navigation, patient entry, consent review, and access history.

| Before | Implemented behavior |
| --- | --- |
| Framework-demo metadata and a plain centered credential form | Nexa Care metadata, consistent brand, readable surfaces, labeled fields, a clear primary action, keyboard submission, MFA progression, and announced errors/loading |
| Vertical emoji buttons and prominent internal IDs | Responsive action cards, Lucide icons, provider/facility context, and a separate emergency panel |
| Isolated provider pages | Shared provider shell with navigation, current-page indication, skip link, theme control, and sign out |
| Hardcoded pending count and unsupported analytics trends | Removed the fake pending state, static trends, static operational summaries, and date filters that did not affect the API; retained API-returned metrics |
| Web patient entry resolved through a dynamic record route | Dedicated handoff page explaining the supported mobile patient app; no browser device-authority substitute |
| Patient OTP fields without visible labels or clear progression | Two steps, visible accessible labels, large inputs/actions, keyboard avoidance, safe-area spacing, code clearing when changing phone, and explicit failure feedback |
| Repeated consent prose with poorly differentiated timers | Requester/facility block, exact scope chips, purpose, separate response-expiry and access-duration sections, and persistent approve/deny actions |
| Emoji-heavy access-history cards | Consistent icons, semantic routine/emergency badges, category chips, readable contrast, constrained reading width; exact/relative timestamps, refresh, pagination, and empty/error states preserved |
| Native system navigation could be dark while content stayed light | Shared provider follows the system appearance; native content and patient headers use the matching product palette |
| Theme control could cycle through system without a visible change | Explicit light/dark switching based on resolved appearance |
| Recovery inputs used an unsupported Tamagui prop | Two equivalent `readOnly` conditions replace `editable`; recovery services and lifecycle behavior are unchanged |

## Shared components and accessibility

`ScreenContainer`, `Surface`, `ActionButton`, `StatusBadge`, `InlineNotice`, `Brand`, `ScreenHeader`, `SectionHeading`, `FormField`, `LoadingState`, and `AuthFrame` are exported through `@my/ui`. Product colors are shared through `@my/config` and extend the existing Tamagui themes.

The new components use 48px minimum action targets, 52px form fields, visible keyboard focus, semantic headings, field names and hints, live announcements, and status text as well as color. No decorative motion was introduced. The web shell includes a keyboard skip link. Consent actions respect the bottom safe area; patient sign-in retains keyboard dismissal and avoidance.

Seven principal semantic text/background pairs were checked in each palette: minimum contrast 5.18:1 in light and 7.22:1 in dark. This is palette evidence, not a complete accessibility certification. Browser layout checks cover 360px, 768px, and 1440px widths in light/dark. Physical screen-reader, handset, large-text, and platform compilation evidence remain unrun.

## Preserved boundaries

Applicable existing controls include SEC-001/002 (capability transport and server scope), SEC-003/004 (signed consent and replay), SEC-005 (emergency separation), SEC-013/014/015 (privacy, authenticity, provenance), SEC-016/017 (device and session binding), and SEC-021/022 (patient transparency and revocation). The identity-evidence disclosure policy remains unchanged. Regulatory-baseline mappings concern minimum necessary data, distinct consent purposes, provenance, and truthful product claims (REG-001/003/006/011/015); this patch makes no legal-compliance claim.

ProviderAuthContext, API transports/schemas, route guards, consent signing, patient sessions, device enrollment/recovery services, clinical authority, and source/provenance services were not modified. Navigation does not grant access. Approval still goes to biometric verification with only the request identifier; denial still submits the original challenge through signed denial. No new patient data, identifiers, credentials, capabilities, or clinical values are placed in URLs, logs, analytics, or storage. Browser fixtures use explicitly synthetic data and intercepted API responses.

No backend `app/**`, migration, deployment infrastructure, storage/KMS, retention/erasure, audit, HPR/HFR activation, rate-limit, or authority behavior changed. No security or regulatory rule was changed or relaxed. No migration is required.

## Validation

| Check | Result |
| --- | --- |
| Focused existing login/history/viewport tests | 32 passed, 0 failed, 0 skipped |
| New consent presentation/signing-boundary tests | 5 passed, 0 failed, 0 skipped |
| Final `yarn test` | Next: 2 files / 6 tests; app: 36 files / 227 tests. Total 233 passed, 0 failed, 0 skipped |
| Patient login tests after the final ARIA adjustment | 3 passed, 0 failed, 0 skipped; already included in the full-suite count above |
| Next TypeScript | Passed; also checked by the production build |
| Expo TypeScript | Passed after the two recovery-input prop corrections |
| Changed-file Biome lint | 27 source/test/config files passed |
| `yarn verify:next-build` | Passed, including shared workspace builds and Next production compilation |
| Dedicated production browser checks | 5 passed, 0 failed, 0 skipped; synthetic API responses, local build assets, Edge/Chromium |
| `git diff --check` | Passed |
| Remote Frontend CI | Not run: patch is uncommitted and unpublished |

During development, checks caught and resolved new-component Tamagui compatibility issues, missing test icon stubs, a theme toggle cycle, and shell content sizing. The first full test invocation collided with the visual-preview dev server; after that server was stopped, the full command passed. The production loopback URL correctly failed the existing origin guard; the browser harness now serves loopback assets at the reserved origin pinned by the verification build. No application origin guard was weakened.

Backend suites, PostgreSQL/Redis qualification, security scans, migrations, AWS/Textract/FHIR qualification, and historical backend slices were deliberately not rerun, as requested. Native Android/iOS compilation, physical devices, live OTP delivery, real biometric signing, and live clinical integrations were not run and are not claimed.

To repeat the dedicated browser check after `yarn verify:next-build`, start the built Next app on loopback port 3101, then run from `apps/next`:

```powershell
$env:NEXA_BROWSER_CHANNEL = 'msedge' # or an installed Playwright Chromium
$env:NEXA_UI_BASE_URL = 'https://doctor.example.test'
$env:NEXA_UI_ASSET_ORIGIN = 'http://127.0.0.1:3101'
node ../../node_modules/@playwright/test/cli.js test -c playwright.product.config.ts
```

Screenshots are local artifacts under `.test-results/ui-product/` at the repository root. They show synthetic provider context and are not live hospital evidence.

## File inventory

All paths below are relative to `nexa-client/`.

| Area | Files changed or added |
| --- | --- |
| Design system | `packages/ui/src/NexaPrimitives.tsx`, `packages/ui/src/index.tsx`, `packages/ui/src/SwitchThemeButton.tsx` |
| Theme configuration | `packages/config/src/nexaTheme.ts`, `packages/config/src/tamagui.config.ts`, `packages/config/src/index.ts` |
| Theme providers/native layouts | `packages/app/provider/index.tsx`, `packages/app/provider/NextTamaguiProvider.tsx`, `apps/expo/app/_layout.tsx`, `apps/expo/app/patient/_layout.tsx` |
| Web shell/branding/entry | `apps/next/app/layout.tsx`, `apps/next/app/product.css`, `apps/next/app/doctor/layout.tsx`, `apps/next/app/patient/login/page.tsx`, `packages/app/features/doctor/ProviderShell.tsx` |
| Provider screens | `packages/app/features/doctor/DoctorLoginScreen.tsx`, `packages/app/features/doctor/DoctorDashboardScreen.tsx`, `packages/app/features/dashboard/DashboardScreen.tsx` |
| Patient screens | `packages/app/features/patient/PatientLoginScreen.tsx`, `packages/app/features/patient/ConsentRequestScreen.tsx`, `packages/app/features/patient/AccessHistoryScreen.tsx`, `packages/app/features/patient/PatientRecoveryScreen.tsx` |
| Validation | `packages/app/features/patient/ConsentRequestScreen.test.tsx`, `packages/app/features/patient/PatientLoginScreen.test.tsx`, `test/setup.ts`, `apps/next/e2e/product-polish.spec.ts`, `apps/next/playwright.product.config.ts` |
| Handoff | `docs/product-ui-foundation.md` |

## Deferred UI work and remaining limits

The clinical record viewer's content layout, NFC state presentation, emergency confirmation form, extraction/adjudication review ergonomics, patient health timeline, enrollment/results/devices, and secondary/admin surfaces retain their current workflows. Provider routes benefit from the shell and theme foundation, but those screens have not received a complete redesign. Historical authentication flows were traced and retained.

The new browser handoff intentionally does not implement native patient sign-in in Next. Navigation remains subject to existing server authority. Native presentation is supported by shared code and type/component checks, not physical-device evidence. The full product redesign and universal accessibility qualification are not claimed by this first batch.
