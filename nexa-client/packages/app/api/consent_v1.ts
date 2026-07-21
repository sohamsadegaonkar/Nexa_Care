import { apiClient, ApiError, type ApiResponse } from '../utils/apiClient'

export type ConsentAssurance =
  | 'standard'
  | 'push_approved'
  | 'biometric_confirmed'
  | 'bypassed_emergency'
  | 'standard_fallback_from_push'

export interface RoutineConsentRequest {
  patient_id: string
  purpose: 'TREATMENT' | 'PAYMENT' | 'OPERATIONS' | 'RESEARCH'
  scope: string[]
  assurance_level?: 'standard'
  assurance_evidence?: Record<string, unknown>
}

export interface BreakGlassRequest {
  patient_uuid: string
  hospital_id: string
  clinician_id: string
  reason: string
  justification: string
}

export interface ConsentResponse {
  consent_token: string
  expires_at: string
}

export type ConsentErrorCode =
  | 'CONSENT_INVALID'
  | 'CONSENT_UNAUTHORIZED'
  | 'CONSENT_UNAVAILABLE'
  | 'CONSENT_FAILED'

export class ConsentError extends Error {
  public readonly code: ConsentErrorCode
  public readonly status?: number

  constructor(message: string, code: ConsentErrorCode, status?: number) {
    super(message)
    this.name = 'ConsentError'
    this.code = code
    this.status = status
  }
}

/**
 * Issue routine consent (v1.0 architecture)
 */
export async function issueRoutineConsentV1(
  payload: RoutineConsentRequest
): Promise<ConsentResponse> {
  try {
    const response = await apiClient.post<
      ConsentResponse,
      ApiResponse<ConsentResponse>,
      RoutineConsentRequest
    >('/api/v2/consent/routine/issue', {
      ...payload,
      assurance_level: payload.assurance_level || 'standard',
    })
    return response.data
  } catch (error: unknown) {
    if (error instanceof ApiError) {
      const status = error.status
      throw new ConsentError(
        error.message || 'Failed to issue consent',
        error.code === 'PATIENT_APPROVAL_REQUIRED' ? 'CONSENT_UNAUTHORIZED' : 'CONSENT_FAILED',
        status
      )
    }
    throw new ConsentError('Consent request failed', 'CONSENT_FAILED')
  }
}

/**
 * Issue emergency break-glass consent (v1.0)
 */
export async function issueBreakGlassV1(
  payload: BreakGlassRequest
): Promise<ConsentResponse> {
  try {
    const response = await apiClient.post<
      ConsentResponse,
      ApiResponse<ConsentResponse>,
      BreakGlassRequest
    >('/api/v2/consent/break-glass/issue', payload)
    return response.data
  } catch (error: unknown) {
    if (error instanceof ApiError) {
      throw new ConsentError(
        error.message || 'Break-glass failed',
        'CONSENT_FAILED',
        error.status
      )
    }
    throw new ConsentError('Break-glass request failed', 'CONSENT_FAILED')
  }
}

/**
 * Validate consent token
 */
export async function validateConsent(
  consentToken: string,
  patientUuid?: string
): Promise<ConsentResponse | null> {
  try {
    const params = new URLSearchParams()
    if (patientUuid) params.append('patient_id', patientUuid)
    const query = params.toString()

    const response = await apiClient.get<ConsentResponse>(
      `/api/v2/consent/validate${query ? `?${query}` : ''}`,
      { headers: { 'X-Consent-Token': consentToken } }
    )
    return response.data
  } catch {
    return null
  }
}