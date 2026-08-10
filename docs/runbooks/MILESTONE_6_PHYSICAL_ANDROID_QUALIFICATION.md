# Milestone 6 Physical Android Qualification

## Focused real document extraction

This section adds one focused synthetic-document check. It does not repeat the
completed approval, denial, expiry, revocation, NFC, or routine-consent suites.
Never use real patient PHI.

### Preconditions

- Deploy the exact backend/frontend candidate to an isolated HTTPS pilot.
- Set `DOCUMENT_EXTRACTION_PROVIDER=aws_textract` and
  `DOCUMENT_AI_AWS_REGION=ap-south-1`.
- Give the runtime identity only `textract:AnalyzeDocument` through the normal
  AWS SDK credential chain/IAM role. Do not configure access keys in app files.
- Apply migrations through `20260810_identity_review`.
- Use an authorized synthetic patient and a physical Android device that
  reports `device` in `adb devices -l`.
- Prepare exactly one synthetic, single-page PDF/PNG/JPEG, at most 10 MB, with
  no real PHI. Its identity must match the synthetic patient.

### Focused sequence

1. Sign in as the clinician and select **Upload & AI Extract**.
2. Select the synthetic patient. Confirm purpose is locked to
   `document_processing` and scope to `documents`.
3. On physical Android, inspect and sign the request as the synthetic patient.
4. Confirm the doctor browser claims access and opens upload with only
   `workflow_id` in navigation. Refresh must discard the memory-only
   capability and require new consent.
5. Upload the synthetic document. Confirm “upload stored; extraction pending”
   is distinct from a completed provider result.
6. Record only provider `aws_textract`, HTTP/job states, candidate count,
   evidence completeness, identity result, lane, and reason codes. Never record
   values, source text, patient IDs, hashes, tokens, credentials, or payloads.
7. Confirm each candidate shows authentic field confidence, page, source text,
   and bounding-box availability. Document confidence remains separate.
8. Accept `SOURCE_ONLY` or `QUARANTINE` as safe. Confirm AUTO_COMMIT is off.
9. For `SOURCE_ONLY`, open source adjudication, manually enter an allowed value,
   submit, and explicitly commit. Do not use the legacy review queue.
10. Confirm the timeline/audit provenance is `human_adjudicated` and contains
    no raw PHI, capabilities, AWS credentials, payloads, or document hashes.

### Pass and stop rules

Pass only when Textract was genuinely called and authentic evidence is visible.
Stop with the exact safe blocker when AWS credentials, isolated deployment,
migration, synthetic resources, or physical device are unavailable. Never
substitute mocks or localhost for this focused physical proof.
