import type {
  AdjudicatedClinicalField,
  AdjudicationOutcome,
  AdjudicationReasonCode,
} from '../../utils/apiClient'

export const REASONS_BY_OUTCOME: Record<AdjudicationOutcome, readonly AdjudicationReasonCode[]> = {
  ACCEPTED: ['SOURCE_VERIFIED', 'MANUAL_TRANSCRIPTION', 'CORRECTED_AGAINST_SOURCE'],
  REJECTED: ['NOT_CLINICAL_DATA', 'ILLEGIBLE_SOURCE', 'DUPLICATE_OBSERVATION', 'SOURCE_MISMATCH'],
  NEEDS_SPECIALIST_REVIEW: [
    'SPECIALIST_INTERPRETATION_REQUIRED',
    'AMBIGUOUS_SOURCE',
    'OUT_OF_SUPPORTED_SCOPE',
  ],
}

export const VITAL_TYPES = [
  'BLOOD_PRESSURE',
  'HEART_RATE',
  'TEMPERATURE',
  'SPO2',
  'RESPIRATORY_RATE',
] as const

export type ClinicalEntryDraft = {
  kind: 'VITAL' | 'LAB_RESULT'
  vitalType: (typeof VITAL_TYPES)[number]
  testName: string
  numericValue: string
  unit: string
  referenceRange: string
  isAbnormal: boolean
  effectiveAt: string
  pageNumber: string
  provenanceType: 'HUMAN_TRANSCRIBED' | 'HUMAN_VERIFIED'
}

export type ClinicalEntryValidation =
  | { ok: true; field: AdjudicatedClinicalField }
  | { ok: false; message: string }

export function validateClinicalEntry(draft: ClinicalEntryDraft): ClinicalEntryValidation {
  const numericValue = Number(draft.numericValue)
  if (!draft.numericValue.trim() || !Number.isFinite(numericValue)) {
    return { ok: false, message: 'Enter a finite numeric value.' }
  }
  const unit = draft.unit.trim()
  if (!unit || unit.length > 32) {
    return { ok: false, message: 'Enter the unit shown in the source document.' }
  }
  const effectiveAt = new Date(draft.effectiveAt)
  if (!draft.effectiveAt || Number.isNaN(effectiveAt.getTime())) {
    return { ok: false, message: 'Enter a valid observation date and time.' }
  }
  let pageNumber: number | null = null
  if (draft.pageNumber.trim()) {
    pageNumber = Number(draft.pageNumber)
    if (!Number.isInteger(pageNumber) || pageNumber < 0) {
      return { ok: false, message: 'Page number must be a whole number of zero or greater.' }
    }
  }
  const common = {
    reviewer_entered_value: numericValue,
    normalized_value: numericValue,
    unit,
    effective_at: effectiveAt.toISOString(),
    page_number: pageNumber,
    provenance_type: draft.provenanceType,
  } as const
  if (draft.kind === 'VITAL') {
    if (!VITAL_TYPES.includes(draft.vitalType)) {
      return { ok: false, message: 'Select a supported vital type.' }
    }
    return { ok: true, field: { kind: 'VITAL', vital_type: draft.vitalType, ...common } }
  }
  const testName = draft.testName.trim()
  if (!testName || testName.length > 128) {
    return { ok: false, message: 'Enter the laboratory test name shown in the source.' }
  }
  const referenceRange = draft.referenceRange.trim()
  if (!referenceRange || referenceRange.length > 64) {
    return { ok: false, message: 'Enter the supported laboratory reference range.' }
  }
  return {
    ok: true,
    field: {
      kind: 'LAB_RESULT',
      test_name: testName,
      reference_range: referenceRange,
      is_abnormal: draft.isAbnormal,
      ...common,
    },
  }
}

export function validateReasonCodes(
  outcome: AdjudicationOutcome,
  reasons: readonly AdjudicationReasonCode[]
): boolean {
  return (
    reasons.length <= 4 &&
    new Set(reasons).size === reasons.length &&
    reasons.every((reason) => REASONS_BY_OUTCOME[outcome].includes(reason))
  )
}

export function adjudicationFingerprint(
  outcome: AdjudicationOutcome,
  fields: readonly AdjudicatedClinicalField[],
  reasons: readonly AdjudicationReasonCode[]
): string {
  return JSON.stringify({ outcome, fields, reason_codes: reasons })
}
