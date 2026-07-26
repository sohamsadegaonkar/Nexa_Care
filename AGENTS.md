# Nexa Care Repository Agent Contract

This file applies to the entire repository.

## Mandatory reading order

Before inspecting, designing, generating, modifying, or reviewing code, every coding agent must read:

1. [Security Non-Regression Standard](docs/governance/SECURITY_NON_REGRESSION.md)
2. [India Regulatory Baseline](docs/governance/INDIA_REGULATORY_BASELINE.md)
3. [Engineering Constitution](docs/governance/NEXA_CARE_ENGINEERING_CONSTITUTION.md)

The agent must identify which rules apply to its task. Before changing code, it must briefly record the affected security invariants, regulatory controls, product/engineering principles, required validation, and whether the task changes patient data, consent, access, audit, identity, AI extraction, storage, erasure, or emergency access.

## Conflict priority

1. Direct system, developer, and user instructions
2. Applicable laws and binding regulations
3. `SECURITY_NON_REGRESSION.md`
4. `INDIA_REGULATORY_BASELINE.md`
5. `NEXA_CARE_ENGINEERING_CONSTITUTION.md`
6. Existing feature documentation
7. Local conventions

Choose the safer interpretation of conflicting internal rules and flag the conflict.

## Completion report

Every code task must report files changed; applicable invariants; tests run, passed, and skipped; validation not run; remaining risks; migration status when relevant; and whether any security or regulatory rule changed. Never report a skipped test as passed.

## Prohibited behavior

Agents must not weaken security to pass tests, conceal production defects through test changes, add release-critical `xfail`, claim real PostgreSQL/Redis/KMS/object-storage/device evidence from mocks, expose health data or secrets in logs/URLs/errors, put patient data in URLs, use production data for tests, perform destructive database work without an explicitly disposable target, silently expand scope, claim legal compliance without legal review, or commit/push/merge/deploy without explicit authorization.

## Documentation maintenance

Update the relevant governance document in the same patch when a change creates an invariant, fixes a new security defect, introduces a regulated feature, changes consent/retention/breach/AI/clinical scope, or changes a mandatory engineering rule.
