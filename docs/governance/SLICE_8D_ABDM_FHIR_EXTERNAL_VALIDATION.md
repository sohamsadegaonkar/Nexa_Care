# Slice 8D — ABDM FHIR External Validation

Status: **EXTERNAL PROFILE VALIDATOR QUALIFIED FOR THE SUPPORTED SOURCE-CONDITIONAL MAPPINGS; PARTNER SANDBOX EXCHANGE NOT_RUN**

Authoritative base: `a9c08be92df40a3eed2b78af0a557d6a8da1c9cb`

Qualification branch: `slice-8d-abdm-fhir-external-validation`

## Scope

Slice 8D upgrades the Slice 7D internal `nexa-fhir-r4-base-v1` contract with a
real external validator gate against the published ABDM FHIR implementation
guide. It does not convert unknown source facts into fabricated FHIR facts.

Stable target selected for this qualification:

- FHIR base: `4.0.1`
- ABDM package: `ndhm.in#6.5.0`
- published profile base: `https://nrces.in/ndhm/fhir/r4/StructureDefinition/`
- externally checked Nexa resource types: `Condition`, `MedicationRequest`,
  `Observation`, `AllergyIntolerance`

The separate NRCeS preview package is not treated as the stable target.

## External validator implementation

`.github/workflows/fhir-abdm-external-validation.yml` generates a deterministic
synthetic export through the real `app.services.fhir_converter.generate_fhir_bundle`
path, runs Nexa's internal R4 validator, downloads the HL7 validator CLI, loads
`ndhm.in#6.5.0`, and validates every emitted resource against its corresponding
published ABDM profile.

Qualification inputs are synthetic only. No patient PHI is uploaded to the
validator or terminology service.

## Findings and remediation

The first run that reached the external validator exposed two material profile
errors:

1. the ABDM `AllergyIntolerance` profile requires `clinicalStatus` when the
   resource is not entered-in-error;
2. the ABDM `MedicationRequest` profile requires `requester`.

Nexa did **not** remediate these errors by inventing values. The converter now:

- emits allergy clinical status only when the source record explicitly contains
  one of `active`, `inactive`, or `resolved`;
- emits MedicationRequest requester only when the source contains a valid
  provider UUID, rendered as a Practitioner reference;
- never substitutes the provider performing a later export as the original
  medication requester;
- never reinterprets Nexa workflow risk/severity as FHIR allergy criticality;
- leaves both fields absent for legacy/unknown records rather than manufacturing
  profile conformance.

Focused tests in `tests/test_fhir_abdm_mapping.py` pin the positive mappings and
the non-fabrication behavior.

## Measured external evidence

Diagnostic runs before the final successful mapping were not treated as PASS:

- run `34398530389`: fixture-generator direct-entrypoint import failure;
- run `34398719028`: existing internal validator direct-entrypoint import failure;
- the first run reaching the official profile validator exposed the two source-
  authority gaps above.

First successful external profile-validation run:

- workflow: `ABDM FHIR External Validation`
- run: `34399696465`
- source head: `29297657f84f30a21b2a05d1b825e4a4cf02b605`
- result: **SUCCESS**
- generated Nexa resources: `5`
- internal Nexa R4 validation: **PASS**
- HL7 validator version: `6.10.4`
- validator SHA-256:
  `1106b9d58f9e363e47bea7c4fc065841e5fc91fe9d062775c3bfdd212bd653cc`
- ABDM package loaded: `ndhm.in#6.5.0`
- terminology server connection: `https://tx.fhir.org`
- external profile validation result: `ABDM_FHIR_EXTERNAL_VALIDATION=PASS`

Because tests/governance commits changed the branch after that first measured
success, the exact PR head still requires a fresh external-validator run plus
normal Backend and Frontend CI before merge.

## Qualification boundary

A successful validator run establishes that Nexa's supported source-conditional
mappings can produce resources accepted by the selected published ABDM 6.5.0
profiles. It does **not** claim that every historical Nexa row contains the facts
required by those profiles. Missing requester or allergy lifecycle authority is
not silently backfilled.

Still explicitly NOT_RUN / not claimed:

- exchange with an actual ABDM or partner FHIR sandbox endpoint;
- partner authentication, transport, capability negotiation, or production
  interoperability;
- certification by NHA/NRCeS;
- migration of historical medication/allergy rows to invented profile facts;
- validation against the preview/draft next-version IG.

Those boundaries are independent of the external profile-validator PASS.
