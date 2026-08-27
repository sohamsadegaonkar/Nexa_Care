import { apiClient, ApiError, type ApiResponse } from '../utils/apiClient'

export interface BreakGlassConsentIssueRequest {
  patient_id: string
  reason_code: BreakGlassReasonCode
  justification: string
  requested_scope?: string[]
}

export type BreakGlassReasonCode =
  | 'UNCONSCIOUS_PATIENT'
  | 'LIFE_THREATENING_EMERGENCY'
  | 'PATIENT_UNABLE_TO_CONSENT'
  | 'CARDIAC_ARREST'
  | 'ANAPHYLAXIS'
  | 'SURGICAL_EMERGENCY'
  | 'PATIENT_INCAPACITATED'
  | 'SYSTEM_OR_CONSENT_SERVICE_UNAVAILABLE'
  | 'OTHER_CLINICALLY_JUSTIFIED_EMERGENCY'

export const BREAK_GLASS_REASON_OPTIONS: ReadonlyArray<{
  value: BreakGlassReasonCode
  label: string
}> = [
  { value: 'UNCONSCIOUS_PATIENT', label: 'Unconscious patient' },
  { value: 'LIFE_THREATENING_EMERGENCY', label: 'Life-threatening emergency' },
  { value: 'PATIENT_UNABLE_TO_CONSENT', label: 'Patient unable to consent' },
  { value: 'CARDIAC_ARREST', label: 'Cardiac arrest' },
  { value: 'ANAPHYLAXIS', label: 'Anaphylaxis' },
  { value: 'SURGICAL_EMERGENCY', label: 'Surgical emergency' },
  { value: 'PATIENT_INCAPACITATED', label: 'Patient incapacitated' },
  { value: 'SYSTEM_OR_CONSENT_SERVICE_UNAVAILABLE', label: 'Consent service unavailable' },
  { value: 'OTHER_CLINICALLY_JUSTIFIED_EMERGENCY', label: 'Other clinically justified emergency' },
]

export interface BreakGlassConsentIssueResponse {
  consent_token: string
  expires_at?: string
  approved_scope: string[]
  policy_version: string
  authorization_ref: string
}

export type BreakGlassConsentErrorCode =
  | 'BREAK_GLASS_INVALID_REQUEST'
  | 'BREAK_GLASS_UNAUTHORIZED'
  | 'BREAK_GLASS_UNAVAILABLE'
  | 'BREAK_GLASS_FAILED'

export class BreakGlassConsentError extends Error {
  public readonly code: BreakGlassConsentErrorCode
  public readonly status?: number
  public readonly retryable: boolean

  public constructor(
    message: string,
    code: BreakGlassConsentErrorCode,
    retryable: boolean,
    status?: number
  ) {
    super(message)
    this.name = 'BreakGlassConsentError'
    this.code = code
    this.status = status
    this.retryable = retryable
  }
}

/**
 * Requests an emergency break-glass consent capability for a patient record.
 */
export async function requestBreakGlassConsent(
  patientId: string,
  reasonCode: BreakGlassReasonCode,
  justification: string
): Promise<BreakGlassConsentIssueResponse> {
  const normalizedPatientId = patientId.trim()
  const normalizedReasonCode = reasonCode.trim()
  const normalizedJustification = justification.trim()

  if (!normalizedPatientId || !normalizedReasonCode || !normalizedJustification) {
    throw new BreakGlassConsentError(
      'Patient ID, reason code, and justification are required.',
      'BREAK_GLASS_INVALID_REQUEST',
      false
    )
  }

  try {
    const response = await apiClient.post<
      BreakGlassConsentIssueResponse,
      ApiResponse<BreakGlassConsentIssueResponse>,
      BreakGlassConsentIssueRequest
    >('/api/v2/consent/break-glass/issue', {
      patient_id: normalizedPatientId,
      reason_code: reasonCode,
      justification: normalizedJustification,
    })

    return response.data
  } catch (error: unknown) {
    if (error instanceof ApiError) {
      const status = error.status

      if (status === 400 || status === 422) {
        throw new BreakGlassConsentError(
          'Emergency override request is missing required information.',
          'BREAK_GLASS_INVALID_REQUEST',
          false,
          status
        )
      }

      if (status === 401 || status === 403) {
        throw new BreakGlassConsentError(
          'Provider session is not authorized for emergency override.',
          'BREAK_GLASS_UNAUTHORIZED',
          false,
          status
        )
      }

      if (status === 503) {
        throw new BreakGlassConsentError(
          'Emergency override service is temporarily unavailable.',
          'BREAK_GLASS_UNAVAILABLE',
          true,
          status
        )
      }
    }

    throw new BreakGlassConsentError(
      'Unable to request emergency override.',
      'BREAK_GLASS_FAILED',
      false
    )
  }
}
