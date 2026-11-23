# Nexa Care Repository Agent Contract

This file applies to the entire repository.

## Mandatory reading order

Before inspecting, designing, generating, modifying, or reviewing code, every coding agent must read:

1. [Security Non-Regression Standard](docs/governance/SECURITY_NON_REGRESSION.md)
2. [India Regulatory Baseline](docs/governance/INDIA_REGULATORY_BASELINE.md)
3. [Identity Evidence Disclosure Policy](docs/governance/IDENTITY_EVIDENCE_DISCLOSURE_POLICY.md)
4. [Engineering Constitution](docs/governance/NEXA_CARE_ENGINEERING_CONSTITUTION.md)

The agent must identify which rules apply to its task. Before changing code, it must briefly record the affected security invariants, regulatory controls, product/engineering principles, required validation, and whether the task changes patient data, consent, access, audit, identity, AI extraction, storage, erasure, or emergency access.

## Conflict priority

1. Direct system, developer, and user instructions
2. Applicable laws and binding regulations
3. `SECURITY_NON_REGRESSION.md`
4. `INDIA_REGULATORY_BASELINE.md`
5. `IDENTITY_EVIDENCE_DISCLOSURE_POLICY.md`
6. `NEXA_CARE_ENGINEERING_CONSTITUTION.md`
7. Existing feature documentation
8. Local conventions

Choose the safer interpretation of conflicting internal rules and flag the conflict.

## Completion report

Every code task must report files changed; applicable invariants; tests run, passed, and skipped; validation not run; remaining risks; migration status when relevant; and whether any security or regulatory rule changed. Never report a skipped test as passed.

## Prohibited behavior

Agents must not weaken security to pass tests, conceal production defects through test changes, add release-critical `xfail`, claim real PostgreSQL/Redis/KMS/object-storage/device evidence from mocks, expose health data or secrets in logs/URLs/errors, put patient data in URLs, use production data for tests, perform destructive database work without an explicitly disposable target, silently expand scope, claim legal compliance without legal review, or commit/push/merge/deploy without explicit authorization.

## Staging fidelity and patient/clinician workflow non-regression

**Case study:** [Staging must not replace product screens with fictional demos (24 Nov 2026)](docs/governance/PATIENT_WORKFLOW_NON_REGRESSION_20261124.md). Nexa Care staging means a **deployable clone of the real application** from the chosen repository revision: original patient/provider screens and route contracts, real API process, isolated clinical data stores and authenticated access control. Staging differs only in infrastructure/secrets, isolation, safely created synthetic accounts/data, and nonfunctional observability/release metadata. Never replace a required real feature with an invented demonstration route, sample-code login, mocked clinical response, or visually convincing but nonfunctional form. Do not solve an outage by deleting the workflow, silently disabling controls or pointing to production services.

Maintain regression tests that compare staging UI entry sources against the actual selected baseline commit and prove **end-to-end** SMS/OTP (only with authorized testing numbers), patient, provider, document and signed-consent flows in a live isolated deployment. Synthetic fixtures may populate the real app in the test environment but must not replace the app. A frontend-only preview or health-200 is not working staging; if dependencies are absent, report the blocked gate, preserve source fidelity and do not mark it ready. Human privacy/security/clinical signoff and physical-device evidence remain separate mandatory gates.

## Documentation maintenance

Update the relevant governance document in the same patch when a change creates an invariant, fixes a new security defect, introduces a regulated feature, changes consent/retention/breach/AI/clinical scope, or changes a mandatory engineering rule.
