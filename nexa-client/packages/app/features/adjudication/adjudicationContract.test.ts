import { describe, expect, it } from 'vitest'
import {
  REASONS_BY_OUTCOME,
  validateClinicalEntry,
  validateReasonCodes,
  type ClinicalEntryDraft,
} from './adjudicationContract'

const base: ClinicalEntryDraft = {
  kind: 'VITAL',
  vitalType: 'HEART_RATE',
  testName: '',
  numericValue: '72',
  unit: 'bpm',
  referenceRange: '',
  isAbnormal: false,
  effectiveAt: '2026-07-29T10:30',
  pageNumber: '1',
  provenanceType: 'HUMAN_VERIFIED',
}

describe('adjudication clinical form contract', () => {
  it('builds only the supported vital and laboratory discriminated unions', () => {
    expect(validateClinicalEntry(base)).toMatchObject({
      ok: true,
      field: { kind: 'VITAL', vital_type: 'HEART_RATE', normalized_value: 72, unit: 'bpm' },
    })
    expect(
      validateClinicalEntry({
        ...base,
        kind: 'LAB_RESULT',
        testName: 'HbA1c',
        unit: '%',
        referenceRange: '4.0-5.6',
      })
    ).toMatchObject({
      ok: true,
      field: { kind: 'LAB_RESULT', test_name: 'HbA1c', reference_range: '4.0-5.6' },
    })
  })

  it.each([
    [{ ...base, numericValue: 'NaN' }, 'finite numeric'],
    [{ ...base, numericValue: 'Infinity' }, 'finite numeric'],
    [{ ...base, unit: '' }, 'unit'],
    [{ ...base, effectiveAt: 'not-a-date' }, 'observation date'],
    [{ ...base, kind: 'LAB_RESULT' as const, testName: '', referenceRange: '0-1' }, 'test name'],
  ])('rejects malformed clinical input', (draft, message) => {
    expect(validateClinicalEntry(draft)).toEqual({
      ok: false,
      message: expect.stringContaining(message),
    })
  })

  it('enforces closed outcome-specific reason codes without duplicates', () => {
    expect(validateReasonCodes('ACCEPTED', ['SOURCE_VERIFIED'])).toBe(true)
    expect(validateReasonCodes('ACCEPTED', ['ILLEGIBLE_SOURCE'])).toBe(false)
    expect(validateReasonCodes('REJECTED', ['ILLEGIBLE_SOURCE', 'ILLEGIBLE_SOURCE'])).toBe(false)
    expect(REASONS_BY_OUTCOME.NEEDS_SPECIALIST_REVIEW).toEqual([
      'SPECIALIST_INTERPRETATION_REQUIRED',
      'AMBIGUOUS_SOURCE',
      'OUT_OF_SUPPORTED_SCOPE',
    ])
  })
})
