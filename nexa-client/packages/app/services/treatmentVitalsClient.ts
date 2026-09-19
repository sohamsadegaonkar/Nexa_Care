import type { TreatmentVitalRequest } from '../utils/apiClient'

function semanticFingerprint(payload: TreatmentVitalRequest): string {
  return JSON.stringify(payload)
}

function newKey(): string {
  const cryptoObj = (globalThis as { crypto?: { randomUUID?: () => string } }).crypto
  const suffix = cryptoObj?.randomUUID
    ? cryptoObj.randomUUID().replace(/-/g, '')
    : `${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 14)}`
  return `tv_${suffix}`
}

/**
 * One in-memory mutation intent. It deliberately survives transport uncertainty
 * but never survives a process restart, provider-session replacement, or an
 * explicit semantic edit.
 */
export class TreatmentVitalMutationIntent {
  private fingerprint: string | null = null
  private key: string | null = null
  private completedFingerprint: string | null = null

  keyFor(payload: TreatmentVitalRequest): string {
    const nextFingerprint = semanticFingerprint(payload)
    if (this.completedFingerprint === nextFingerprint) {
      throw new Error('TREATMENT_VITAL_ALREADY_COMPLETED')
    }
    if (this.fingerprint === nextFingerprint && this.key) return this.key
    this.fingerprint = nextFingerprint
    this.key = newKey()
    return this.key
  }

  markComplete(payload: TreatmentVitalRequest): void {
    const done = semanticFingerprint(payload)
    this.completedFingerprint = done
    if (this.fingerprint === done) {
      this.fingerprint = null
      this.key = null
    }
  }

  clearUncertainIntent(): void {
    this.fingerprint = null
    this.key = null
  }

  resetForSessionReplacement(): void {
    this.fingerprint = null
    this.key = null
    this.completedFingerprint = null
  }
}

export function validateTreatmentVitalRequest(payload: TreatmentVitalRequest): string | null {
  const recorded = Date.parse(payload.recorded_at)
  if (
    !Number.isFinite(recorded) ||
    !/(Z|[+-]\d{2}:\d{2})$/.test(payload.recorded_at)
  ) {
    return 'Observation time must include a timezone.'
  }

  if (payload.kind === 'blood_pressure') {
    if (
      !Number.isInteger(payload.systolic_bp) ||
      !Number.isInteger(payload.diastolic_bp) ||
      payload.systolic_bp < 1 ||
      payload.systolic_bp > 999 ||
      payload.diastolic_bp < 1 ||
      payload.diastolic_bp > 999
    ) {
      return 'Blood pressure values must be whole numbers from 1 to 999 mmHg.'
    }
    return null
  }
  if (payload.kind === 'heart_rate') {
    if (
      !Number.isInteger(payload.beats_per_minute) ||
      payload.beats_per_minute < 1 ||
      payload.beats_per_minute > 999
    ) {
      return 'Heart rate must be a whole number from 1 to 999 bpm.'
    }
    return null
  }
  if (payload.kind === 'temperature') {
    return Number.isFinite(payload.celsius) ? null : 'Temperature must be a finite number in °C.'
  }
  if (payload.kind === 'spo2') {
    return Number.isFinite(payload.percentage) &&
      payload.percentage >= 0 &&
      payload.percentage <= 100
      ? null
      : 'SpO₂ must be a number from 0 to 100%.'
  }
  return 'Unsupported vital observation.'
}
