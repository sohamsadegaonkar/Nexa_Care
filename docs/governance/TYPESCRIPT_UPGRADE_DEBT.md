# Frontend TypeScript Upgrade Debt

Status: **RECORDED / DEFERRED — NOT A CURRENT RELEASE BLOCKER**

Recorded from `main` baseline `5f4b6f73b2c915c6d9fb4140bc8784d9a79bed38` after Slice 9A backend + UI closure.

## Debt item — migrate away from `baseUrl`

Nexa Care currently pins TypeScript `~5.8.3` in `nexa-client/package.json`.

The Next application still contains:

```json
{
  "compilerOptions": {
    "baseUrl": ".",
    "paths": {
      "react-native": ["react-native-web"]
    }
  }
}
```

in `nexa-client/apps/next/tsconfig.json`.

The shared `nexa-client/tsconfig.base.json` also contains `"baseUrl": "."` and may be inherited by multiple workspace packages.

The previously added `"ignoreDeprecations": "6.0"` workaround was removed because it is incompatible with the repository's current TypeScript 5.8 toolchain and caused Next/Vercel build failure. It must not be reintroduced while the repository remains on TypeScript 5.x.

## Why this is deferred

Current production qualification is green without `ignoreDeprecations`:

- Next production build passes;
- workspace frontend tests/builds pass;
- Android and iOS native compile gates pass; and
- Vercel production deployment on the Slice 9A UI merge is green.

Therefore removal of `baseUrl` is an upgrade/migration task, not a reason to continue polishing Slice 9A or destabilize a qualified frontend baseline.

## Required migration procedure

When Nexa Care deliberately upgrades the TypeScript/tooling baseline toward TypeScript 6+, the migration must:

1. inventory every `tsconfig` that defines or inherits `baseUrl`;
2. remove the Next-local `baseUrl` first while preserving valid `paths` mappings where supported;
3. diagnose and fix any real module-resolution dependency rather than suppressing warnings;
4. separately determine whether the shared `nexa-client/tsconfig.base.json` `baseUrl` can be removed without breaking workspace packages or Expo/native resolution;
5. never use `ignoreDeprecations: "6.0"` as a substitute for a compatible compiler/toolchain;
6. run the repository-standard frontend qualification on one exact head:
   - frontend test suites,
   - Next production build,
   - workspace package build,
   - Android native compile,
   - iOS native compile,
   - Vercel deployment;
7. merge only after the exact migration head is green.

## Scope boundary

This debt item does **not** reopen Slice 9A. No registration-recovery backend/UI behavior needs to change for this migration.

The next product/backend work follows the repository product-development order in `docs/context/NEXA_CARE_CODEX_CONTEXT.md`: after registration, proceed to **real secure patient discovery/search**, then the bounded clinical access-session work.