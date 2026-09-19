# Slice 10B.5g — Prescriber Eligibility Source Contract

## Status

```text
PRESCRIBER ELIGIBILITY CONTRACT READY
```

This means the **authority-source contract** is ready for a separately authorized,
bounded prescriber-eligibility implementation.

It does **not** authorize Prescription persistence.

```text
WRITE_PRESCRIPTION PERSISTENCE: NOT IMPLEMENTED / NOT AUTHORIZED BY THIS SLICE
```

The contract is ready because a narrow initial practitioner class can be grounded
in official NMC/NMR/SMR authority and, where no sufficient production machine
interface is available, a controlled Nexa human-attestation decision can bridge
the source-interface gap without allowing provider self-attestation or frontend
authority.

The human-attestation path is a Nexa product/security control. It is not a claim
that Nexa is a regulator, licensing authority, or statutory certifier.

---

# 1. Starting authority

10B.5f governance PR #71 merged at:

```text
572eaec175df375d184f3cdaea5f913256e3094b
```

Authoritative main at branch creation:

```text
572eaec175df375d184f3cdaea5f913256e3094b
```

Inherited single Alembic head:

```text
20260918_treatment_vitals_encounter
```

Fresh branch:

```text
task0/10b5g-prescriber-eligibility-source-contract
```

Frozen upstream invariants remain:

```text
Medication != canonical Prescription
Prescription issuance != active medication state
external prescription document != canonical clinician-authored Prescription
patient correction != clinician prescribing authority
WRITE_PRESCRIPTION != professional entitlement to prescribe
provider authentication != professional verification
professional verification != automatically medication-prescribing entitlement
facility affiliation != prescribing entitlement
```

---

# 2. Research date and legal-engineering boundary

Research cutoff:

```text
2026-09-19
```

This document is an engineering authority contract based on current official
primary material located by the audit. It is not legal advice.

Current regulation can change. A future implementation must re-check the
effective NMC/EMRB/Gazette position at implementation and release time.

When official meaning is uncertain, Nexa fails closed.

---

# 3. Authoritative primary sources audited

## 3.1 National Medical Commission Act, 2019

Source:

```text
India Code
The National Medical Commission Act, 2019
Act No. 30 of 2019
Enactment date: 2019-08-08
https://www.indiacode.nic.in/bitstream/123456789/11820/1/A2019_30.pdf
```

Relevant authority:

- section 2 defines "licence" as licence to practice medicine under section 33;
- section 2 defines "medicine" as modern scientific medicine in all branches,
  including surgery and obstetrics;
- section 31 requires EMRB to maintain the National Register of licensed medical
  practitioners and requires electronic synchronization with State Registers;
- section 31 requires a **separate** National Register for Community Health
  Providers;
- section 32 defines Community Health Provider as a **limited-licence** class;
- section 32 permits a Community Health Provider to prescribe only specified
  medicines independently in primary/preventive healthcare and otherwise only
  under supervision of a section-31(1) medical practitioner;
- section 33 establishes the ordinary licence/enrolment pathway for medical
  practitioners;
- section 34 bars persons not enrolled in a State Register or National Register
  from practising medicine as a qualified medical practitioner, subject to the
  temporary-registration proviso for foreign citizens.

### Engineering consequence

The Act itself proves that:

```text
registration/licence class matters
```

and that:

```text
all registered-looking healthcare identities cannot be collapsed into one
unrestricted prescribing class
```

Community Health Provider is explicit statutory evidence of a limited
prescribing class and is therefore excluded from the initial Nexa capability.

## 3.2 Registration of Medical Practitioners and Licence to Practice Medicine Regulations, 2023

Source:

```text
National Medical Commission / EMRB
Notification No. R.15021/04/2022-EMRB-Reg
dated 2023-05-10
Gazette of India Extraordinary No. 320
published 2023-05-12
https://www.nmc.org.in/MCIRest/open/getDocument?path=/Documents/Public/Portal/LatestNews/DOC-20230514-WA0038_230514_120545.pdf
```

Relevant authority:

- Chapter II separately covers provisional registration for internship;
- regulation 4 defines registration in NMR for licence to practice;
- regulation 5 states NMR contains entries of registered medical practitioners
  from State Registers and publishes registration number, name, registration
  date, place of work, medical qualifications, specialty, year of passing and
  institution/university;
- regulation 6 routes licence grant through State Medical Councils and reflects
  approved licences in both State and National Registers;
- regulation 8 states the licence is valid for five years under the 2023 text,
  requires renewal, and says non-renewal beyond the specified period changes
  the State Register entry to inactive, with that status reflected in NMR;
- regulation 10 requires removal/restoration changes in State Registers to be
  reflected in NMR;
- Chapter IV separately regulates temporary registration of foreign medical
  practitioners.

### Engineering consequence

NMR/SMR is the authoritative registration/licence domain for the initial
modern-medicine RMP policy.

A mere historical registration number is insufficient. Current status matters.

Nexa must **not hard-code a five-year licence assumption** as the only current
status test. A current official register/status decision remains authoritative,
particularly because amendment activity exists in 2026.

## 3.3 Current 2026 registration-regulation activity

Official NMC sources audited:

```text
NMC current Rules & Regulations page
https://www.nmc.org.in/rules-regulations-nmc/

NMC current What's New page
https://nmc.org.in/whats-new
```

The current Rules & Regulations page still lists:

```text
Registration of Medical Practitioners and Licence to Practice Medicine
Regulations, 2023 — dated 2023-05-10
```

The audit also located:

```text
Draft Gazette notification dated 2026-04-07
published/listed by NMC in April 2026
https://www.nmc.org.in/MCIRest/open/getDocument?path=/Documents/Public/Portal/LatestNews/Gazette%20Notification%20dated%2007.04.2026.pdf
```

That document expressly says NMC **proposes** amendments to the 2023
registration/licence regulations and invites objections/suggestions.

The NMC current What's New page also lists on:

```text
2026-08-14
"Draft of (Registration of Medical Practitioners and Licence to Practice
Medicine (Amendment) Regulations 2026)_Inviting comments/objections/
suggestions..."
```

### Current audit conclusion

As of the 2026-09-19 research cutoff, this audit located **draft amendment
activity**, not a later final NMC notification that this engineering review can
safely treat as replacing the 2023 registration/licence regulation.

Therefore:

- the 2023 regulation is the current published regulation used by this contract;
- 2026 draft text is tracked as change risk, not implemented as law;
- implementation/release must re-check for a final Gazette notification.

This is deliberately narrower than asserting that no other legal instrument
could exist.

## 3.4 NMR launch/public notice

Source:

```text
NMC / EMRB Public Notice
F. No. R.15014/Gen/(12)/2023-Regn.
NMR portal inaugurated 2024-08-23
https://nmc.org.in/MCIRest/open/getDocument?path=/Documents/Public/Portal/E_Compendium_of_NMC_2024/14.%20Medical%20Registration.pdf
```

The notice describes NMR as:

- the electronic register mandated by section 31;
- a dynamic database for all allopathic (MBBS) registered doctors in India;
- Aadhaar-linked for individual authenticity;
- interlinked with medical colleges/institutions and State Medical Councils;
- partly public and partly restricted according to role/need.

### Engineering consequence

NMR is an important identity/registration source for the initial policy.

NMR identity linkage does **not**, by itself, create Nexa prescribing
eligibility. Nexa must also determine the registration/licence class, current
status, restrictions, and supported product scope.

## 3.5 NMC public register/search surfaces

Audited official surface:

```text
https://www.nmc.org.in/information-desk/indian-medical-register/
```

The public NMC surface supports searching registered doctors by items including:

- name;
- registration year;
- registration number;
- State Medical Council;
- qualification/search details;
- black-list details.

### Machine-interface conclusion

This audit did **not** locate an officially documented, production NMC
server-to-server API contract that exposes all fields required to safely derive
prescribing eligibility.

A public web search interface is not silently treated as a stable machine API.

No scraping contract is authorized.

## 3.6 Temporary-registration guidelines

Source:

```text
NMC Public Notice — Guidelines on Temporary Registration
dated 2024-01-18
https://www.nmc.org.in/wp-content/uploads/2026/02/PublicNoticeGuidelinesonTemporaryRegistration18012024.pdf
```

The guidelines state that temporary registration is purpose-specific and that
the foreign medical practitioner works under the sponsor's supervision.
Responsibilities are limited by the granted purpose, qualification and
competence.

### Engineering consequence

Foreign temporary registration is excluded from the initial Nexa prescribing
capability.

Its scope is purpose/institution/supervision dependent and cannot be safely
collapsed into ordinary unrestricted RMP prescribing eligibility.

## 3.7 Professional-conduct material

Current NMC Rules & Regulations page lists:

```text
NMC Registered Medical Practitioner (Professional Conduct) Regulations, 2023
dated 2023-08-02
```

and the:

```text
2023-08-23 amendment notification keeping the 2023 professional-conduct
regulations in abeyance
```

The official NMC site also continues to publish the earlier:

```text
Indian Medical Council (Professional Conduct, Etiquette and Ethics)
Regulations, 2002 — amended through 2016-10-08
https://www.nmc.org.in/rules-regulations/code-of-medical-ethics-regulations-2002/
```

The legacy code contains prescription-related duties, including registration
number on prescriptions and rational prescribing.

### Engineering consequence

The 2023 professional-conduct regulation is **not** treated as active
prescribing authority by this contract because NMC's own current page records
it as kept in abeyance.

The earlier ethics code is corroborative context for prescription practice, but
it is not used as the sole current entitlement source.

The actual eligibility predicate rests on current licence/register authority
plus Nexa's narrow product policy.

## 3.8 ABDM Healthcare Professionals Registry

Official source:

```text
Ayushman Bharat Digital Mission — Healthcare Professionals Registry
https://ahpr.abdm.gov.in/about
```

ABDM describes HPR as a comprehensive repository of healthcare professionals
across modern and traditional systems that connects professionals to India's
digital-health ecosystem.

### Engineering consequence

```text
HPR enrollment != NMC/SMR licence to practice modern medicine
HPR enrollment != medication-prescribing entitlement
```

HPR may later assist with identity/profile correlation if a qualified official
machine contract is available.

It is not the initial legal/professional authority source for
`PRESCRIBE_MEDICATION`.

---

# 4. Registration-class matrix

| Practitioner class | Official basis | Initial Nexa prescribing eligibility | Reason |
|---|---|---|---|
| Full/current modern-medicine RMP with recognised primary medical qualification and current ordinary NMR/SMR licence, no known restriction | NMC Act ss. 31, 33, 34; 2023 registration regulations | **SUPPORTED, subject to governed verification** | Narrow class with ordinary licence to practice modern medicine |
| Existing legacy IMR practitioner validly carried into NMR/SMR and currently licensed | NMC Act s. 33 proviso + current register status | **SUPPORTED, subject to governed verification** | Historical registration path may be valid, but current status must be proven |
| Foreign medical graduate holding a full ordinary current Indian NMR/SMR licence | NMC Act / recognised-qualification and registration framework | **SUPPORTED only if the resulting Indian licence is the same full ordinary class** | Eligibility follows current Indian licence class, not nationality |
| Provisional registration / internship | 2023 regulations Chapter II | **UNSUPPORTED** | Training-limited status is not ordinary independent prescribing authority |
| Community Health Provider / limited licence | NMC Act s. 32 | **UNSUPPORTED** | Statute expressly limits prescribing scope and supervision conditions |
| Foreign temporary registration | 2023 regulations Chapter IV + 2024 guidelines | **UNSUPPORTED** | Purpose-, institution-, duration- and supervision-bounded |
| Other temporary / limited / restricted class | Official class-specific authority required | **UNSUPPORTED** | Unknown or bounded scope cannot be generalized |
| Suspended registration | Current official adverse state | **UNSUPPORTED** | No current positive authority |
| Revoked / removed registration | NMC/SMR removal/adverse state | **UNSUPPORTED** | No current licence authority |
| Expired / inactive licence | 2023 regulation 8 / current official status | **UNSUPPORTED** | Not entitled to practice under the current status |
| Unknown registration class | Insufficient evidence | **UNSUPPORTED** | Fail closed |
| HPR-only professional without current NMR/SMR ordinary modern-medicine licence | ABDM HPR vs NMC authority distinction | **UNSUPPORTED** | HPR is not the licensing source |
| Allied/traditional-system professional outside the NMC modern-medicine register | Different statutory/regulatory systems | **UNSUPPORTED IN V1** | Nexa v1 deliberately does not encode all Indian professional systems |

---

# 5. Machine-verifiable minimum

The future capability needs evidence for the following facts.

| Required fact | Official evidence exists? | Current documented machine contract found? | Classification | Contract decision |
|---|---|---|---|---|
| Practitioner identity | NMR identity/registration domain; NMR notice describes Aadhaar-linked identity internally | No complete production API contract located | **PARTIAL** | Human verifier must bind exact provider to official registration evidence |
| Registration number | NMR/SMR | No stable production API contract located | **AVAILABLE as official evidence / PARTIAL machine** | Required |
| Registration authority / State Medical Council | NMR/SMR | No complete machine contract located | **AVAILABLE as official evidence / PARTIAL machine** | Required |
| Recognised modern-medicine primary qualification | NMR public-data design includes qualifications | Machine field semantics not qualified | **AVAILABLE/PARTIAL** | Required for v1 class |
| Registration/licence class | Separate ordinary/provisional/temporary/limited concepts exist in law/regulation | No single authoritative machine field contract located | **PARTIAL** | Must be explicitly established; unknown class fails |
| Current licence/registration status | Inactive/removal/restoration semantics exist in 2023 regulation and synchronized registers | No sufficient production API contract located | **PARTIAL** | Current positive state required |
| Licence validity period | 2023 regulation contains renewal/validity semantics | Exact current machine field not qualified; 2026 draft activity creates change risk | **PARTIAL** | Use current official source; do not infer solely from old issuance date |
| Suspension/revocation/removal | Register/removal/blacklist mechanisms exist | Complete restriction API not found | **PARTIAL** | Any adverse signal blocks |
| Fine-grained restrictions | May exist through competent authority decisions | No complete source contract located | **UNKNOWN/PARTIAL** | Unknown/restricted fails |
| System of medicine / professional category | NMR policy is modern/allopathic RMP; qualifications are present | No complete production API field contract located | **PARTIAL** | Initial policy accepts only proven modern-medicine ordinary RMP |
| Drug-specific prescribing limitation | Drug/regulatory law is separate; no single register entitlement field identified | No | **NOT_AVAILABLE** | Capability is not a controlled-drug/formulary authorization engine |
| Exact "may prescribe medication" boolean | Not identified as an NMR/HPR machine field | No | **NOT_AVAILABLE** | Nexa derives only a narrow internal capability from the full governed evidence set |

## Machine-only verdict

```text
MACHINE-ONLY PRESCRIBER ELIGIBILITY: NOT READY
```

The source data domain is authoritative, but the audit did not locate a
production machine interface whose semantics are sufficient for automatic
granting.

No scraping, undocumented endpoint, browser automation or HPR shortcut is
authorized to fill that gap.

---

# 6. NMR / SMR / HPR / Nexa relationship

## NMR

Role:

```text
national registration/licence register for NMC modern-medicine practitioners
```

Legal source: NMC Act section 31 and the 2023 registration/licence regulation.

## SMR

Role:

```text
State registration/licence source
```

The NMC Act requires NMR and State Registers to be electronically synchronized.

The 2023 regulation routes licence grant/renewal through State Medical Councils
and requires resulting state changes to reflect nationally.

## HPR

Role:

```text
digital-health ecosystem professional identity/profile registry
```

HPR covers healthcare professionals across multiple systems.

It is not treated as the legal current-licence or prescribing-entitlement source.

## Nexa ProfessionalVerification

Role:

```text
Nexa server-owned evidence/lifecycle projection of professional trust
```

It is not the regulator.

Its current fields can establish registration identity, verification lifecycle,
validity, source/evidence and adverse signals, but not the narrower prescribing
scope decision.

## Prescribing-policy source

The initial product policy is derived from:

```text
current ordinary modern-medicine NMR/SMR licence authority
+
absence of disqualifying/restricting official evidence
+
Nexa's deliberately narrow supported-prescribing scope
```

---

# 7. Initial supported practitioner policy

## Supported class

Nexa v1 prescribing eligibility is intentionally restricted to:

> A provider whose exact identity is bound to a current, ordinary/full
> modern-medicine Registered Medical Practitioner licence in the competent
> NMR/SMR registration system, whose recognised primary medical qualification
> is established, whose licence/registration is not temporary, provisional,
> limited, inactive, expired, suspended, revoked, removed or otherwise
> restricted for the contemplated basic outpatient prescribing scope, and whose
> existing Nexa professional/facility/affiliation trust is current.

This policy is intentionally narrower than every category that may lawfully
perform some prescribing activity in India.

## Explicitly unsupported

- Community Health Providers;
- provisional/internship registrants;
- temporary foreign registrants;
- unknown/limited licence classes;
- restricted licence classes;
- suspended/revoked/removed/inactive/expired registrations;
- HPR-only identities;
- allied/traditional-system professions outside this NMC modern-medicine policy;
- any class whose official semantics cannot be confidently understood.

Unknown means deny.

---

# 8. Prescribing scope and medication restrictions

The future capability means only:

```text
provider is professionally eligible, under Nexa's v1 policy, to enter the
bounded basic medication-prescription workflow
```

It does **not** mean:

- permission to prescribe every drug;
- permission for controlled/narcotic substances;
- permission for NDPS/specially controlled medicine workflows;
- permission for chemotherapy protocols;
- permission for specialist-restricted therapies;
- permission for inpatient medication administration;
- permission for medication administration orders;
- permission for every system-of-medicine category;
- drug/formulary authorization.

## Important implementation consequence

The frozen v1 PrescriptionItem currently contains free-text:

- medication_name;
- strength;
- frequency.

That shape alone cannot safely classify all controlled, narcotic, specialty
restricted or highly regulated drugs.

Therefore:

```text
PRESCRIBER ELIGIBILITY CONTRACT READY
!=
UNBOUNDED DRUG-SCOPE WRITE AUTHORIZED
```

A future Prescription implementation must separately prove its medication-scope
gate, for example through a controlled allowlist/coded medication policy or
another bounded mechanism.

The eligibility capability must never be treated as a universal drug entitlement.

---

# 9. Human-attestation fallback decision

## Decision

```text
GOVERNED HUMAN ATTESTATION IS APPROVED AS THE INITIAL SOURCE-CONTRACT FALLBACK
```

This is recommended because:

1. the underlying NMC/NMR/SMR authority is official and suitable for the narrow
   full-RMP policy;
2. a sufficient documented production machine API was not located;
3. Nexa already has strong server-owned provider identity and
   ProfessionalVerification primitives;
4. a controlled human decision can fail closed when official source semantics
   are ambiguous;
5. no provider self-attestation or frontend authority is needed.

## Minimum positive-attestation evidence

An authorized Nexa verifier must establish all of:

- exact Nexa provider identity;
- exact registration authority;
- exact registration number;
- current official NMR/SMR or competent-council evidence;
- recognised modern-medicine primary qualification;
- ordinary/full RMP registration/licence class;
- current positive registration/licence state;
- no known temporary/provisional/limited classification;
- no current inactive/expired/suspended/revoked/removed state;
- no official restriction that conflicts with the supported Nexa scope;
- existing ProfessionalVerification binding matches the same authority/number;
- source checked timestamp;
- primary source reference/URL/identifier;
- reviewer identity;
- bounded decision validity.

If the official source does not expose enough information to establish any
required fact, the verifier cannot mark the provider ELIGIBLE.

## Evidence retention

The model may retain, subject to privacy/security/legal review:

- normalized source reference;
- source class;
- authority code;
- evidence digest/hash;
- structured decision facts;
- reference to existing append-only ProviderTrustVerificationEvidence;
- a protected evidence-object reference when lawful/necessary.

Do not put raw registry pages or full licence-document content in general audit
events.

---

# 10. Human-attestation security invariants

1. Provider cannot attest themselves.
2. Provider cannot choose their eligibility state.
3. Frontend cannot send a boolean or capability that grants eligibility.
4. Reviewer must be separately server-authorized for the prescriber-eligibility
   review function.
5. Reviewer identity is durable.
6. Positive decision is versioned, reviewable and revocable.
7. A newer official adverse signal overrides an older positive decision.
8. Expiry/recheck failure is fail closed.
9. Source-unavailable state cannot silently become or remain positive forever.
10. Old decisions/evidence are never destructively overwritten.
11. Corrected provider profile text does not change eligibility by itself.
12. HPR profile state does not grant eligibility.
13. Patient consent does not grant professional eligibility.
14. Treatment Session `WRITE_PRESCRIPTION` does not grant professional
    eligibility.
15. Audit events are structural and value-safe.

---

# 11. Prescriber eligibility lifecycle

Recommended bounded lifecycle:

```text
PENDING
ELIGIBLE
RECHECK_DUE
RESTRICTED
SUSPENDED
REVOKED
EXPIRED
SOURCE_UNAVAILABLE
```

No other state is needed for v1.

## PENDING

Meaning:

- no completed positive eligibility review; or
- evidence is incomplete/ambiguous.

Capability:

```text
DENY
```

## ELIGIBLE

Meaning:

- all positive criteria are currently proven;
- decision has not reached `next_review_at`;
- decision has not reached `valid_until`;
- ProfessionalVerification and existing trust remain current;
- no adverse/restriction evidence is known.

Capability:

```text
MAY contribute to PRESCRIBE_MEDICATION
```

This is the **only** eligibility state that may contribute the future capability.

## RECHECK_DUE

Meaning:

- required re-verification deadline reached.

Capability:

```text
DENY
```

No automatic grace for new prescription writes.

## RESTRICTED

Meaning:

- provider has current professional authority but a known restriction means
  Nexa cannot safely grant its v1 basic prescribing scope.

Capability:

```text
DENY
```

## SUSPENDED

Meaning:

- competent source/current Nexa trust records suspension.

Capability:

```text
DENY
```

## REVOKED

Meaning:

- prescribing eligibility explicitly revoked or underlying professional
  authority removed/revoked.

Capability:

```text
DENY
```

## EXPIRED

Meaning:

- eligibility decision, licence authority, or required underlying verification
  expired.

Capability:

```text
DENY
```

## SOURCE_UNAVAILABLE

Meaning:

- a required initial/recheck source cannot be evaluated at the point a current
  decision is required.

Capability:

```text
DENY
```

---

# 12. Recheck and expiry policy

## Initial v1 human-attestation cadence

Nexa policy:

```text
human-attested prescribing eligibility maximum validity = 30 days
```

This 30-day ceiling is a Nexa internal high-risk-write safety policy, **not a
statutory licence period**.

A decision must end earlier if any authoritative bound is earlier.

Conceptually:

```text
valid_until =
min(
  reviewed_at + 30 days,
  current official licence/registration expiry if explicitly known,
  ProfessionalVerification.next_review_at if earlier,
  ProfessionalVerification.registration_valid_until if earlier,
  any stronger official restriction/decision expiry
)
```

`next_review_at` must be no later than `valid_until`.

A production implementation may choose to schedule verification before the
deadline, but after `next_review_at` the prescribing capability fails closed
until a new positive decision exists.

## Why not use the statutory five-year text as the Nexa review interval?

Because:

- source status can change between licence-renewal dates;
- suspension/removal/adverse signals can occur earlier;
- 2026 amendment activity exists;
- prescribing is a higher-risk write capability;
- the human-attestation source path is not a live machine subscription.

---

# 13. Source-unavailable policy

## Initial verification

```text
required source unavailable
-> no ELIGIBLE decision
-> deny PRESCRIBE_MEDICATION
```

## Periodic recheck

No new grace period is created.

If the required source cannot be checked when recheck is due:

```text
ELIGIBLE -> SOURCE_UNAVAILABLE
PRESCRIBE_MEDICATION -> denied
```

An existing positive decision may remain positive only until its already-recorded
`next_review_at`; source failure must not extend that time.

## Adverse evidence

Any explicit suspension, revocation, removal, expiry, inactive status or
applicable restriction overrides source-outage handling immediately.

There is no "patient already approved the session" exception.

---

# 14. Revocation / adverse propagation

Future prescribing eligibility must be revalidated at mutation time.

## Future Treatment Session request

If professional prescribing eligibility is not ELIGIBLE:

```text
request for WRITE_PRESCRIPTION -> deny
```

Other independently authorized operations are not automatically denied solely
because a prescribing-specific scope decision is restricted, unless the broader
ProfessionalVerification/facility/affiliation trust also fails.

## Pending/approved but unclaimed Treatment Session

Claim/mint must re-check current prescribing eligibility.

If eligibility was lost after patient signature:

```text
claim WRITE_PRESCRIPTION authority -> deny
```

Patient signature does not freeze professional authority.

## Already claimed ClinicalAccessSession

A previously claimed session cannot be treated as a permanent prescriber
licence snapshot.

Every future prescription mutation must re-check eligibility.

A future implementation may additionally revoke/invalidate affected session
authority proactively, but mutation-time revalidation remains mandatory.

## Existing issued prescriptions

Loss of current eligibility does **not** delete or rewrite historical
Prescription issuance facts.

Historical records remain immutable clinical history.

---

# 15. Future capability predicate

A future typed capability may be named:

```text
ClinicalCapability.PRESCRIBE_MEDICATION
```

or a vocabulary-consistent equivalent.

This slice does not add the enum.

The capability may evaluate true only when all of the following are current:

```text
provider account + credential active
AND current ProfessionalVerification acceptable
AND current PrescribingEligibilityDecision.state == ELIGIBLE
AND practitioner_class == FULL_RMP_MODERN_MEDICINE
AND now < next_review_at
AND now < valid_until
AND no blocking restriction/adverse state
AND verified facility current
AND affiliation ACTIVE and valid
AND required contact/session/MFA assurance current
```

For a Treatment Session write, that capability remains only one layer.

The final write still separately requires:

```text
patient-signed Treatment Session
AND exact WRITE_PRESCRIPTION
AND durable ConsentGrant
AND durable ClinicalAccessSession
AND canonical Encounter
AND server-derived context
AND bounded prescription semantics
AND idempotency
AND value-free audit
AND atomic transaction
```

---

# 16. Data-model recommendation

## Decision

Choose:

```text
B. separate durable PrescribingEligibilityDecision domain
```

Do not put prescribing eligibility directly inside `ProfessionalVerification`.

## Why separate?

Prescribing eligibility has:

- a narrower policy meaning than general professional verification;
- an independent review cadence;
- independent restriction semantics;
- potentially different source evidence;
- different revocation triggers;
- high-risk capability consequences;
- a need for append-only decision history.

Collapsing it into ProfessionalVerification would make:

```text
professionally verified
```

too easy to confuse with:

```text
allowed by Nexa's prescribing policy
```

## Conceptual future decision object

Design only:

```text
PrescribingEligibilityDecision
- decision_id
- provider_id
- professional_verification_id
- decision_version
- state
- practitioner_class
- registration_authority_code
- registration_number_normalized
- source_class
- source_reference
- evidence_digest / evidence reference
- decision_basis_code
- restriction_codes (closed vocabulary)
- reviewed_by_actor_id
- reviewed_at
- next_review_at
- valid_until
- supersedes_decision_id
- created_at
```

Prefer append-only decision rows.

A current projection/index may later identify the latest authoritative decision,
but old decisions are not destructively overwritten.

Where possible, the decision should reference existing
`ProviderTrustVerificationEvidence` rather than copy raw evidence.

No schema is implemented in 10B.5g.

---

# 17. Audit contract

Prescriber-eligibility lifecycle actions require structural audit.

Allowed structural metadata may include:

- provider internal ID;
- decision ID/version;
- decision type;
- lifecycle transition;
- source class;
- registration authority code;
- reviewer actor ID;
- policy version;
- evidence-reference identifier/digest.

Do not put in audit:

- patient identity/data;
- medication data;
- prescription content;
- raw licence-document contents;
- raw registry pages;
- credentials/tokens/secrets;
- unrestricted source payloads.

Audit must not become a secondary professional-record dump.

---

# 18. Relationship to existing ProfessionalVerification grace

Existing Nexa ProfessionalVerification contains bounded recheck/grace semantics
for general clinical trust.

Prescribing eligibility is intentionally stricter in v1:

```text
RECHECK_DUE or SOURCE_UNAVAILABLE -> no new prescribing capability
```

No additional prescription-specific grace is proposed.

If broader ProfessionalVerification itself becomes suspended, revoked, expired,
stale or otherwise ineligible, prescribing eligibility necessarily fails even if
an older prescribing decision row says ELIGIBLE.

A narrower positive decision can never override broader trust loss.

---

# 19. Current official-regulation conclusions as of 2026-09-19

1. The NMC Act distinguishes ordinary licensed medical practitioners from
   limited-licence Community Health Providers and gives the latter restricted
   prescribing semantics.

2. NMR/SMR is the registration/licence authority domain for the initial
   modern-medicine RMP policy.

3. The NMC 2023 registration/licence regulation remains the current published
   regulation identified on NMC's Rules & Regulations page by this audit.

4. NMC has published 2026 **draft** amendment activity. The April draft expressly
   says the Commission "proposes" amendments and seeks comments. The current NMC
   What's New page on 2026-08-14 also labels the registration/licence amendment
   as a draft inviting comments.

5. This audit did not locate a later final 2026 NMC registration/licence
   notification that it can safely treat as replacing the 2023 regulation.

6. NMC's 2023 Professional Conduct Regulations are listed by NMC with a
   2023-08-23 amendment keeping them in abeyance. They are therefore not used as
   this capability's current authority source.

7. The official NMR notice describes a dynamic allopathic/MBBS registered-doctor
   database linked to Aadhaar and State Medical Councils.

8. The official temporary-registration guidance confirms temporary foreign
   registration is purpose/supervision bounded.

9. ABDM HPR is a broader digital-health professional registry and is not treated
   as NMC licence authority.

10. No official production machine contract exposing all v1 eligibility
    semantics was located in this audit.

## Required release-time revalidation

Before any production prescriber-eligibility implementation is released, the
owner must re-check:

- NMC Rules & Regulations;
- NMC Gazette/e-Gazette notices;
- current NMR/SMR semantics;
- any final 2026 registration/licence amendment;
- relevant competent-State-Medical-Council status semantics;
- any prescribing-specific restriction source used by the product.

---

# 20. Source-contract outcome

## Machine source

```text
NOT SUFFICIENTLY DOCUMENTED FOR AUTOMATIC GRANT
```

## Human-attestation fallback

```text
DEFENSIBLE AND APPROVED FOR THE NARROW V1 CLASS
```

provided all security/evidence/expiry requirements in this document are
implemented and qualified.

## Overall outcome

```text
PRESCRIBER ELIGIBILITY CONTRACT READY
```

Meaning:

> Task 0 now has a defensible explanation of **why** a provider can be treated as
> professionally eligible for Nexa's bounded prescribing workflow.

It does **not** mean:

- prescription persistence is implemented;
- every medicine can be prescribed;
- Nexa has regulatory certification;
- HPR alone proves entitlement;
- a provider's role string can grant capability;
- a human reviewer may guess missing source facts.

---

# 21. No application implementation in this slice

10B.5g creates no:

- Prescription table;
- PrescriptionItem table;
- prescribing capability enum;
- PrescribingEligibilityDecision table;
- migration;
- NMR/HPR/SMR registry connector;
- WRITE_PRESCRIPTION route;
- provider prescribing UI;
- drug/formulary engine.

The only repository change is this governance/source-contract document.

---

# 22. Exact next Task-0 action

The next Task-0 action is a **separately authorized bounded
prescriber-eligibility implementation slice**, before Prescription persistence.

That slice should implement and qualify, in this order:

1. append-only/versioned PrescribingEligibilityDecision persistence;
2. separately authorized Nexa verifier workflow;
3. evidence/reference binding to current ProfessionalVerification;
4. the closed eligibility lifecycle and 30-day maximum human-attestation
   validity;
5. fail-closed source-unavailable/recheck behavior;
6. adverse/revocation propagation;
7. server-owned typed prescribing capability predicate;
8. request/claim/mutation-time revalidation seams;
9. structural audit and qualification tests.

Only after that provider-authority layer is independently qualified should a
later Task-0 slice be allowed to implement canonical Prescription persistence.

A later Prescription implementation must also close the separate medication
scope problem before it can accept unbounded free-text drug names.

---

# 23. Final invariant

```text
Nexa must know why a provider is professionally entitled to prescribe before it
ever accepts a Prescription mutation.
```
