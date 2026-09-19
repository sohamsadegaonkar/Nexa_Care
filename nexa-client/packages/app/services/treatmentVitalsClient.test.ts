import { describe, expect, it, vi } from 'vitest'
import { TreatmentVitalMutationIntent, validateTreatmentVitalRequest } from './treatmentVitalsClient'

const first = {
  kind: 'heart_rate' as const,
  beats_per_minute: 72,
  recorded_at: '2026-09-19T09:00:00Z',
}

describe('TreatmentVitalMutationIntent', () => {
  it('reuses one key across a lost-response retry', () => {
    vi.stubGlobal('crypto', { randomUUID: () => '11111111-1111-4111-8111-111111111111' })
    const intent = new TreatmentVitalMutationIntent()
    expect(intent.keyFor(first)).toBe(intent.keyFor(first))
  })

  it('rotates the key when the semantic observation changes', () => {
    let index = 0
    vi.stubGlobal('crypto', {
      randomUUID: () =>
        (++index === 1
          ? '11111111-1111-4111-8111-111111111111'
          : '22222222-2222-4222-8222-222222222222'),
    })
    const intent = new TreatmentVitalMutationIntent()
    const key1 = intent.keyFor(first)
    const key2 = intent.keyFor({ ...first, beats_per_minute: 73 })
    expect(key2).not.toBe(key1)
  })

  it('blocks accidental replay after a known completed write', () => {
    const intent = new TreatmentVitalMutationIntent()
    intent.keyFor(first)
    intent.markComplete(first)
    expect(() => intent.keyFor(first)).toThrow('TREATMENT_VITAL_ALREADY_COMPLETED')
  })

  it('accepts only the bounded representation contract without clinical interpretation', () => {
    expect(validateTreatmentVitalRequest(first)).toBeNull()
    expect(
      validateTreatmentVitalRequest({
        kind: 'spo2',
        percentage: 101,
        recorded_at: '2026-09-19T09:00:00Z',
      })
    ).toContain('0 to 100')
    expect(
      validateTreatmentVitalRequest({
        kind: 'temperature',
        celsius: 37,
        recorded_at: '2026-09-19T09:00:00',
      })
    ).toContain('timezone')
  })
})
