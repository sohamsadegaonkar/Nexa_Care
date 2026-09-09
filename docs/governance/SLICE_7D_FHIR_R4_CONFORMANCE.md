# Slice 7D — FHIR R4 Conformance and Interoperability

Status: **INTERNAL BASE-R4 CONTRACT CANDIDATE — EXTERNAL VALIDATION NOT_RUN**

Baseline `main`: `03aa3bed8097dac989fe81ce34097d17da62f58d`

Branch: `slice-7d-fhir-conformance`

## Objective

Move Nexa's FHIR export from shape-only route coverage to an explicit,
machine-checkable internal FHIR R4 contract without claiming an external
implementation-guide, partner sandbox, or certification result that has not
actually been run.

Core rule:

```text
FHIR-LIKE JSON != INTERNAL R4 CONTRACT PASS != EXTERNAL PROFILE PASS != PARTNER INTEROPERABILITY
```

## Starting implementation verified from the repository

Before Slice 7D, `/api/v2/fhir/export/{patient_id}` already:

- required `ClinicalCapability.RECORD_READ` and active patient consent;
- loaded current structured patient records first;
- used the deprecated `nexa_clinical` shard only as an empty-structured-data fallback;
- aborted the export if the audit ledger write failed;
- emitted a FHIR `Bundle` containing `Condition`, `MedicationRequest`,
  `Observation`, and `AllergyIntolerance` resources.

However, the converter built lightweight dictionaries and the response model
validated only top-level Bundle fields. There was no machine gate for the exact
resource subset, references, required bound codes, timestamps, Quantity unit
rules, or resource-specific structural constraints.

## Concrete R4 defect found and fixed

The previous `AllergyIntolerance` converter always emitted a `reaction` object
containing only `severity`.

FHIR R4 requires every `AllergyIntolerance.reaction` to contain at least one
`manifestation`. Nexa's current `Allergy` table does not store an authoritative
reaction manifestation, so creating one would invent clinical data.

Slice 7D therefore removes the fabricated severity-only reaction. The allergy
propensity still exports `code`, patient reference, clinical status, and
criticality. A future reaction may be emitted only after the source model holds
an actual manifestation.

## Declared export contract

The machine-readable contract is:

`docs/interop/fhir-r4-export-contract.json`

Contract identifier:

`nexa-fhir-r4-base-v1`

FHIR version:

`4.0.1`

Current emitted base resources are exactly:

- `Condition`;
- `MedicationRequest`;
- `Observation`;
- `AllergyIntolerance`.

The Bundle type is `collection`. Patient references remain logical external
references of the form `Patient/{patient_uuid}`; a Patient resource is not
currently emitted in the collection.

No ABDM, IPS, US Core, national, hospital-specific, or partner implementation
guide is declared by this contract.

## Internal validator

`app/services/fhir_conformance.py` provides a value-safe internal validator for
the declared subset. It checks, among other invariants:

- Bundle resource type, collection type, FHIR IDs, unique `fullUrl` identities,
  and exact `fullUrl`/resource-ID correspondence;
- an allow-list containing only the four declared resource types;
- every patient subject/patient reference is a UUID reference and, at export
  time, matches the exact patient requested by the route;
- recognized R4 condition clinical-status coding;
- MedicationRequest required status, intent, medication concept, patient
  reference, and date-time shape;
- Observation status, code, exact supported `value[x]` cardinality, patient
  reference, date-time shape, and Quantity coding rules;
- coded Nexa quantities use UCUM and never carry a code without a system;
- AllergyIntolerance patient/code/status/criticality constraints;
- every emitted allergy reaction, if a future source legitimately creates one,
  has a required manifestation and only a recognized severity code.

The export route runs this validator before auditing or returning the bundle. An
internal validation failure returns a generic server error and does not expose
row values or detailed validation diagnostics to the caller.

`scripts/validate_fhir_r4_bundle.py` exposes the same contract as a deterministic
JSON report for sanitized fixtures or deployment evidence.

## Unit and terminology handling

For observations, numeric values are promoted to `valueQuantity` only when the
source unit has an explicit, reviewed UCUM mapping in the converter. The
Quantity carries the display unit, UCUM system, and UCUM code.

Values that cannot be represented unambiguously as one numeric Quantity remain
lossless `valueString` values. For example, a blood-pressure source value such
as `130/85 mmHg` is not falsely collapsed into a single Quantity.

Unknown units are not guessed into UCUM codes.

## Provenance boundary

Structured Nexa rows carry source/confidence/risk/source-document metadata, and
the export audit event records the exporting provider and facility. Those facts
do not yet provide all authoritative semantics needed to manufacture a FHIR
`Provenance.agent`, nor does the current Allergy model provide a reaction
manifestation.

Therefore `nexa-fhir-r4-base-v1` does **not** claim to emit a `Provenance`
resource. This is explicit in the machine-readable contract rather than silently
inventing clinical or actor semantics.

If a future interoperability target requires FHIR Provenance, the source data
model and profile contract must be extended explicitly and requalified.

## Authorization non-regression

Slice 7D does not weaken the existing export authority boundary:

```text
CURRENT PROVIDER TRUST
+ RECORD_READ CAPABILITY
+ ACTIVE PATIENT CONSENT
+ EXACT PATIENT RECORD SOURCE
+ INTERNAL FHIR CONTRACT PASS
+ SUCCESSFUL AUDIT WRITE
= EXPORT MAY RETURN
```

A conformance failure cannot be bypassed by consent, provider role, or a valid
HTTP request.

## Qualification levels

### Internal base-R4 contract

Can be qualified in repository CI. This proves only that Nexa-generated bundles
satisfy the declared local contract and its regression fixtures.

### Official/full FHIR validator or implementation-guide validation

Status: **NOT_RUN**.

The local validator is deliberately narrower than the complete official FHIR R4
validator and does not substitute for one.

### External partner/sandbox interoperability

Status: **NOT_RUN / NO TARGET SELECTED**.

A future external PASS must identify the exact implementation guide/profile set,
validator/target version, immutable Nexa commit, sanitized fixture set, and
measured result. It cannot be inferred from internal CI.

## Exit gate for this software sub-slice

The internal 7D implementation may merge only after the exact final head has:

- Ruff success;
- Backend Partition A/B/C success with zero qualification skips;
- Frontend CI success where repository policy runs it;
- regression coverage for valid structured exports, patient-reference binding,
  Quantity/UCUM handling, malformed allergy reaction rejection, empty bundles,
  and route fail-closed behavior;
- no unresolved review finding that weakens provider trust, consent, audit, or
  FHIR validation.

Merging this software does not change external FHIR validation from `NOT_RUN`.

## Explicit nonclaims

This slice does not claim:

- external FHIR certification;
- ABDM/India-specific FHIR profile compliance;
- partner EHR interoperability;
- production exchange qualification;
- a FHIR Provenance resource;
- live HPR/HFR qualification;
- Textract accuracy PASS;
- physical Slice 6I completion.
