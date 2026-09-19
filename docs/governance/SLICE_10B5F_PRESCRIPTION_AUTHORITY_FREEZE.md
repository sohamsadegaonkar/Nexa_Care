# Slice 10B.5f — Prescription Authority Freeze

## Status

```text
IMPLEMENTATION BLOCKED
```

Exact blocker:

```text
WRITE_PRESCRIPTION IMPLEMENTATION BLOCKED — PRESCRIBER ELIGIBILITY SOURCE NOT YET DEFINED
```

This slice is **governance/design only**.

It creates no:

- Prescription table;
- PrescriptionItem table;
- Alembic migration;
- WRITE_PRESCRIPTION route;
- provider prescription form;
- client mutation;
- second executable Treatment Session write family.

## Starting authority

PR #70 merged as:

```text
05dde001fde464223480cc3da230e592117354a1
```

Post-merge authoritative main:

```text
05dde001fde464223480cc3da230e592117354a1
```

Inherited Alembic head:

```text
20260918_treatment_vitals_encounter
```

10B.5e remains authoritative for the domain direction:

```text
Medication != canonical Prescription
Prescription 1 -> N PrescriptionItem
```

A canonical Prescription is one immutable clinician-authored issuance event bound to patient, authoritative provider, canonical Encounter, server-owned issuance time, and exact Treatment Session `WRITE_PRESCRIPTION` authority.

---

# 1. Executive decision

The Prescription aggregate is sufficiently defined for schema design, but **prescription persistence is not implementation-ready** because Nexa does not yet possess a durable server-owned fact whose semantics are strong enough to say:

> this verified provider is currently professionally entitled to prescribe medication.

The repository does possess strong provider-trust primitives:

- authenticated provider account;
- active ProviderCredential;
- contact assurance;
- ProfessionalVerification;
- facility verification;
- active provider/facility affiliation;
- server-owned ClinicalCapability checks;
- Treatment Session patient authorization.

Those facts are individually and collectively valuable, but the current professional-verification schema proves **professional registration identity/current verification state**, not medication-prescribing entitlement.

Therefore this slice does **not** add a capability such as `PRESCRIBE_MEDICATION`.

Doing so now would convert an undefined professional-policy assumption into executable authorization.

---

# 2. Prescriber-specific eligibility audit

## 2.1 ProviderIdentity

Authoritative model:

```text
app/models/provider.py
ProviderIdentity
```

Relevant fields include:

- provider UUID;
- legacy role;
- account status;
- display name;
- legacy `medical_registration_number`;
- free-text specialty;
- contact information;
- link to ProviderCredential;
- link to ProfessionalVerification;
- hospital affiliations.

### Finding

`ProviderIdentity.medical_registration_number` is explicitly treated by the repository as a **legacy claim**.

It is not sufficient for prescribing authority.

The free-text `role` and `specialty` fields are also not controlled prescribing-authority facts.

They cannot safely answer:

- whether this professional category is legally/product-policy eligible to prescribe medication;
- whether prescribing authority is limited by registration class;
- whether an active restriction exists;
- whether a specialty-specific limitation applies;
- whether the registration authority's semantics actually confer prescribing authority.

## 2.2 ProviderCredential

Authoritative model:

```text
ProviderCredential
```

It provides authentication state such as:

- login identity;
- password material;
- active/inactive credential state;
- MFA configuration;
- password lifecycle;
- lockout state.

### Finding

Authentication proves **who authenticated**, not **what professional acts they may perform**.

ProviderCredential cannot establish prescribing eligibility.

## 2.3 ProfessionalVerification

Authoritative model:

```text
ProfessionalVerification
```

This is the repository's real professional-trust source.

It stores:

- `provider_id`;
- `registration_authority_code`;
- `registration_number_normalized`;
- verification lifecycle status;
- verification method/source/reference;
- identity-binding method/status;
- registration validity window;
- verified / last-checked / next-review timestamps;
- recheck/grace state;
- adverse-signal state;
- reviewer/decision metadata;
- server provenance evidence linkage;
- version.

The model explicitly states:

```text
ProviderIdentity.medical_registration_number remains a legacy claim.
Only ProfessionalVerification may contribute professional trust to clinical eligibility.
```

### Strength

This is genuine durable server-owned evidence for **professional verification**.

The verification architecture also supports:

- append-only evidence;
- registration identity matching;
- `CONFIRMED_ACTIVE` observations;
- identity-binding outcomes;
- server provenance;
- lifecycle states such as VERIFIED / SUSPENDED / REVOKED / EXPIRED / RECHECK_DUE;
- bounded validity/review windows.

### Missing prescribing semantic

ProfessionalVerification does **not** persist a controlled fact equivalent to:

```text
profession_class
license_type
scope_of_practice
prescribing_entitlement
medication_prescribing_allowed
prescribing_restrictions
```

Nor does the clinical-eligibility service derive such a fact.

A `VERIFIED` ProfessionalVerification currently means that the repository accepts the provider's professional-verification lifecycle as current enough for general clinical eligibility.

It does not prove that the registered profession/credential confers medication-prescribing authority.

## 2.4 Registration authority and registration number

Provider bootstrap can collect:

- registration authority code;
- normalized registration number.

A provider starts with:

```text
ProfessionalVerificationStatus.NOT_SUBMITTED
```

and no clinical authority merely from registration submission.

This is correct.

However, even after verification, the repository does not define a server-owned mapping such as:

```text
(registration authority, registration class, licence status)
-> PRESCRIBE_MEDICATION eligibility
```

The registration authority code and number identify/qualify a professional record; they are not themselves a prescribing policy.

## 2.5 Verification evidence

`ProviderTrustVerificationEvidence` is append-only and can prove facts such as:

- source;
- adapter version;
- observation time;
- lookup purpose;
- confirmed active/inactive outcome;
- source record reference;
- observed validity;
- identity binding;
- source response digest.

This evidence is intentionally separate from clinical authority.

That is a correct security property.

### Finding

No current evidence field carries a bounded, authoritative **prescribing entitlement semantic**.

A registry response saying a professional registration is active is not automatically equivalent to medication-prescribing permission unless the source contract and product policy explicitly define that meaning.

## 2.6 ClinicalEligibilityService

The current clinical eligibility chain evaluates:

- provider account active;
- ProviderCredential active;
- contact-assurance policy satisfied;
- ProfessionalVerification acceptable/current;
- facility verification current;
- affiliation trust ACTIVE and within validity;
- requested ClinicalCapability granted by server-owned affiliation role mapping;
- interactive/delegated MFA/session requirements where applicable.

This establishes a strong general clinical trust boundary.

### Important limit

`_professional_status(...)` evaluates:

- verification lifecycle;
- registration validity dates;
- verified timestamp;
- review freshness;
- recheck grace.

It does not check:

- profession category;
- prescribing class;
- prescribing restriction;
- medication-prescribing entitlement.

Therefore existing clinical eligibility cannot simply be renamed "prescriber eligibility."

## 2.7 Existing ClinicalCapability vocabulary

Current server-owned clinical capabilities are:

```text
PATIENT_DISCOVER
CONSENT_REQUEST
RECORD_READ
DOCUMENTS_UPLOAD
DOCUMENTS_PROCESS
DOCUMENTS_REVIEW
DOCUMENTS_COMMIT
EMERGENCY_ATTEMPT
```

There is no prescribing-specific capability.

The compatibility mapping currently allows legacy `clinician` affiliation role to map to the existing full capability set.

### Decision

Do **not** add a prescribing capability to that compatibility mapping yet.

Do **not** make:

```text
role == clinician
```

mean:

```text
may prescribe medication
```

The new capability concept is valid, but its grant predicate is not yet defined.

## 2.8 Facility verification and affiliation

Current facility/affiliation checks are necessary but not sufficient.

Verified facility + active provider affiliation means the provider is operating in an accepted organizational context.

It does not prove professional prescribing entitlement.

## 2.9 HPR / HFR and production registry evidence

Repository governance currently states:

```text
INTERNAL REGISTRY BOUNDARY READY;
OFFICIAL SERVER-TO-SERVER TRANSPORT CONTRACT EXTERNALLY BLOCKED
```

The repository further distinguishes:

```text
HPR enrollment != statutory medical council registration
!= active licence standing
!= statutory practice authority by default
```

No production HPR policy is registered, no production HFR policy is registered, and live source qualification remains blocked pending authoritative external contract material.

### Consequence

HPR/HFR cannot be used as a shortcut to invent current prescribing eligibility.

---

# 3. Prescriber eligibility verdict

## Durable facts that exist

A provider can currently prove, server-side:

```text
provider identity
+
active account/credential
+
contact assurance
+
current professional verification lifecycle
+
registration identity/validity evidence
+
facility verification
+
active affiliation
+
current server-owned clinical capability
+
session/MFA assurance
```

## Missing durable fact

The repository cannot currently prove:

```text
CURRENT PROFESSIONAL CREDENTIAL
AUTHORIZES MEDICATION PRESCRIBING
UNDER A DEFINED NEXA POLICY
```

The missing fact is not merely a new enum member.

The missing contract is:

1. what authoritative source establishes prescribing entitlement;
2. what source fields/semantics prove it;
3. what profession/credential classes are allowed;
4. how restrictions/suspension/expiry affect it;
5. whether the entitlement is global or facility/context-specific;
6. how the fact is revalidated;
7. how it maps to a server-owned capability.

## Frozen result

```text
WRITE_PRESCRIPTION IMPLEMENTATION BLOCKED — PRESCRIBER ELIGIBILITY SOURCE NOT YET DEFINED
```

---

# 4. Future prescribing capability semantics

A narrow server-owned prescribing capability remains the preferred architecture once authoritative source semantics exist.

Conceptually:

```text
PRESCRIBE_MEDICATION
```

or a vocabulary-consistent equivalent.

This slice deliberately does **not** choose/add the enum name.

## Capability must mean

> Current server-owned professional evidence and product policy establish that this provider may issue medication prescriptions in the current clinical context.

## Capability must not mean

- provider logged in;
- provider has MFA;
- provider role says clinician/doctor;
- provider supplied a registration number;
- ProfessionalVerification merely exists;
- facility affiliation exists;
- patient granted Treatment Session `WRITE_PRESCRIPTION`;
- Treatment Session token is valid.

## Separation of authorities

```text
professional prescribing eligibility
!=
patient-signed WRITE_PRESCRIPTION operation authority
```

Both will be required.

The patient's signed operation answers:

> May this qualified provider perform this operation in this bounded Treatment Session?

Professional prescribing eligibility answers:

> Is this provider professionally eligible to perform medication prescribing at all under Nexa's server-owned policy?

Neither substitutes for the other.

---

# 5. Route field decision

Frozen:

```text
DEFER ROUTE IN V1
```

The extraction stack recognizes administration route, but the repository has no controlled canonical route vocabulary for Prescription persistence.

Do not add arbitrary free-text route to the first canonical PrescriptionItem.

Future route support requires a separate bounded vocabulary/normalization decision.

---

# 6. Duration field decision

Frozen:

```text
DEFER DURATION IN V1
```

The repository has extraction evidence for duration, but no canonical bounded type/unit contract for a clinician-authored PrescriptionItem.

Do not introduce uncontrolled free-text duration.

---

# 7. Instructions decision

Frozen:

```text
DEFER GENERIC INSTRUCTIONS IN V1
```

The first bounded item remains:

- medication_name;
- strength;
- frequency.

Do not add generic prescription free text simply because other EHR systems support it.

A future instruction field requires:

- size limit;
- normalization policy;
- client rendering contract;
- immutable/idempotency semantics;
- clinical safety review.

---

# 8. Lifecycle decision

Frozen:

```text
IMMUTABLE ISSUANCE FACT ONLY
```

The initial canonical Prescription has no generic mutable lifecycle status.

Do not invent:

- cancelled;
- superseded;
- discontinued;
- completed.

Those require explicit future transition operations and authorization.

A created Prescription means:

> this clinician issued this medication directive at this time under this authority.

It does not state current medication consumption/status.

---

# 9. Active medication state separation

Frozen invariant:

```text
Prescription issuance != active medication state
```

Creating a canonical Prescription must not automatically:

- insert into legacy `patient_medications`;
- mutate existing Medication rows;
- mark a medication active;
- infer that the patient is taking it;
- remove or supersede historical medication rows.

Medication-state projection remains a separate future domain decision.

This prevents one prescription issuance from silently rewriting longitudinal medication truth.

---

# 10. FHIR decision

Frozen:

```text
INTEROPERABILITY MUST NOT FABRICATE CLINICAL LIFECYCLE
```

The current FHIR converter already uses the fail-closed pattern for legacy medication-derived MedicationRequest:

```text
status = unknown
```

when Nexa lacks lifecycle authority.

That posture remains correct for the first canonical Prescription unless a later lifecycle contract provides a more precise status.

## Future canonical Prescription can improve

A future Prescription would authoritatively provide:

- requester/provider;
- authoredOn / issued_at;
- medication item semantics.

## It does not yet provide

- active/completed/cancelled/discontinued lifecycle state.

Therefore FHIR export must not infer `active` merely because a Prescription was issued.

Interoperability requirements must not drive the internal clinical schema into fabricated lifecycle semantics.

---

# 11. External prescription promotion decision

Frozen:

```text
NO DIRECT PROMOTION
```

Patient-uploaded or extracted prescription artifacts do not become a canonical Nexa Prescription.

They remain source evidence / DocumentReference-backed historical information.

## Future clinician workflow

A qualified clinician may potentially review historical evidence and then issue a **new** Nexa Prescription.

That new event would require:

- current professional prescribing eligibility;
- live provider/facility/affiliation trust;
- patient-signed exact `WRITE_PRESCRIPTION`;
- current canonical Encounter;
- server-derived authority;
- bounded Prescription payload;
- idempotency;
- structural audit;
- atomic commit.

The new Prescription is not a conversion or promotion of the external artifact.

It is a new clinician-authored issuance event.

Patient correction of extracted text similarly never becomes clinician authorship.

---

# 12. Frozen aggregate

10B.5e's aggregate direction remains frozen.

## Conceptual Prescription

```text
Prescription
- prescription_id
- patient_id
- provider_id
- encounter_id
- issued_at
- server-owned origin/provenance
```

## Conceptual PrescriptionItem

```text
PrescriptionItem
- prescription_item_id
- prescription_id
- sequence
- medication_name
- strength
- frequency
```

Initial implementation deliberately excludes:

- route;
- duration;
- generic instructions;
- lifecycle status.

No schema is created by this slice.

## Authority ownership

For a live Treatment Session write:

- patient_id is server-derived;
- provider_id is server-derived;
- encounter_id is server-derived;
- issued_at is server-owned;
- origin/provenance is server-owned.

The client supplies only bounded prescription semantics.

Hospital remains authoritative through ClinicalEncounter.

ClinicalAccessSession remains authoritative through ClinicalEncounter.

No duplicate authority field is added merely for convenience.

---

# 13. Frozen future authority graph

A future WRITE_PRESCRIPTION mutation must require all of:

```text
authenticated provider
+
current professional prescribing eligibility
+
verified facility / active affiliation / contact / credential trust
+
current provider session + required MFA assurance
+
patient-signed Treatment Session
+
exact WRITE_PRESCRIPTION
+
durable ConsentGrant
+
durable ClinicalAccessSession
+
canonical Encounter
+
server-derived patient/provider/hospital context
+
bounded Prescription aggregate
+
durable idempotency
+
TimelineEvent
+
value-free audit
+
atomic transaction
```

No one component independently authorizes prescribing.

In particular:

```text
WRITE_PRESCRIPTION token alone != prescribing authority
patient consent alone != prescribing authority
professional verification alone != prescribing authority
facility affiliation alone != prescribing authority
authentication alone != prescribing authority
```

---

# 14. Future transaction design

Design only:

```text
validate exact Treatment Session authority
-> validate current professional prescribing eligibility
-> lock durable ClinicalAccessSession / ConsentGrant / Encounter authority
-> reserve idempotency
-> create Prescription header
-> create ordered PrescriptionItems
-> create TimelineEvent
-> stage value-free audit outbox event
-> complete idempotency
-> final live authority recheck as required by route/service boundary
-> commit once
```

Any failure:

```text
rollback all
```

No partial PrescriptionItem persistence.

No item-level commit before aggregate completion.

---

# 15. Future idempotency design

Continue using:

```text
public.mutation_idempotency
```

Design operation:

```text
treatment.write_prescription.v1
```

The request hash should bind:

## Server-derived authority

- ClinicalAccessSession ID;
- canonical Encounter ID;
- patient ID;
- provider ID;
- hospital ID;
- exact `WRITE_PRESCRIPTION`.

## Exact aggregate semantics

Ordered item list containing only approved v1 fields:

- sequence;
- medication_name;
- strength;
- frequency.

Same idempotency key + identical semantic aggregate returns the original logical Prescription result.

Same key + changed semantics fails closed.

Server-owned `issued_at` is established by the successful first mutation rather than accepted as caller authority.

---

# 16. Future audit design

Until/unless a separately governed prescription-specific audit event family is approved, initial creation should follow the existing Treatment Session write pattern using structural audit only.

Candidate event:

```text
PATIENT_RECORD_APPEND_SUCCESS
```

Candidate metadata:

```text
clinical_session_id
encounter_id
operation = WRITE_PRESCRIPTION
record_type = prescription
```

Target:

```text
prescription_id
```

Actor:

```text
authoritative provider_id
```

Never include in audit metadata:

- medication name;
- strength;
- frequency;
- instructions;
- route;
- duration;
- prescription body;
- source clinical text;
- Treatment token.

Audit remains structural/value-free.

---

# 17. Timeline design

Future issuance:

```text
TimelineEvent.event_type = PRESCRIPTION
TimelineEvent.event_ref_id = prescription_id
TimelineEvent.occurred_at = issued_at
```

Timeline summary remains bounded, for example:

```text
Prescription issued
```

or:

```text
Prescription issued (N medication items)
```

Timeline text is presentation only.

The Prescription aggregate remains authoritative.

---

# 18. Capability feasibility verdict

## Is a new prescribing capability conceptually justified?

Yes.

A dedicated server-owned capability is materially safer than reusing `RECORD_READ` or assuming all general clinicians may prescribe.

## Is it supportable from existing authoritative facts today?

No.

The repository lacks an authoritative predicate that maps current professional evidence to medication-prescribing entitlement.

Adding a capability today would answer an unresolved policy question by code.

## Decision

Do not add:

```text
PRESCRIBE_MEDICATION
```

or any equivalent enum yet.

First define the authoritative source and policy.

---

# 19. Exact missing authority contract

Before WRITE_PRESCRIPTION implementation can become READY, Task 0 needs a server-owned prescribing-eligibility contract that answers all of:

1. Which durable provider credential/profession types may prescribe?
2. Which authoritative source establishes that classification?
3. Which source outcome proves current prescribing entitlement?
4. Which restrictions or adverse signals remove prescribing eligibility?
5. How are registration expiry, suspension, revocation, and recheck grace handled for prescribing?
6. Is eligibility global or context/facility specific?
7. What exact predicate grants the prescribing capability?
8. How is that predicate revalidated at request time and immediately before commit?
9. What happens when the authoritative source is unavailable?
10. What evidence may be stored without retaining raw registry responses/secrets?

Until that contract exists:

```text
fail closed
```

---

# 20. Implementation readiness

## Outcome

```text
IMPLEMENTATION BLOCKED
```

Reason:

```text
PRESCRIBER ELIGIBILITY SOURCE NOT YET DEFINED
```

The Prescription domain shape is frozen enough for future implementation.

The provider authority prerequisite is not.

No prescription persistence code should be created until that prerequisite is resolved.

---

# 21. Exact next Task-0 action

The next Task-0 action is a **prescriber-eligibility source/policy governance closure**, not Prescription persistence.

Required output of that next governance step:

- identify the authoritative professional source and semantics that prove prescribing entitlement; or
- explicitly define a human-governed durable prescribing-eligibility attestation model if no qualified machine source exists;
- define fail-closed lifecycle/revalidation behavior;
- only then define the server-owned prescribing capability predicate.

Only after that contract is approved should Task 0 authorize a bounded WRITE_PRESCRIPTION implementation slice.

Do not create the Prescription schema, service, route, or provider form before that authority is proven.

---

# 22. Frozen decisions summary

```text
Medication != canonical Prescription
Prescription 1 -> N PrescriptionItem

Prescriber-specific capability:
CONCEPTUALLY REQUIRED, NOT YET SUPPORTABLE

Route:
DEFER

Duration:
DEFER

Generic instructions:
DEFER

Lifecycle:
IMMUTABLE ISSUANCE FACT ONLY

Prescription issuance:
!= ACTIVE MEDICATION STATE

FHIR lifecycle:
FAIL CLOSED / DO NOT INVENT ACTIVE

External prescription promotion:
PROHIBITED

Legacy Medication backfill:
PROHIBITED

WRITE_PRESCRIPTION implementation:
BLOCKED
```

Core invariant:

```text
prescription authority must be proven before prescription persistence is implemented
```
