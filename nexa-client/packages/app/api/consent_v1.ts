import axios, { type AxiosResponse } from 'axios'

import { apiClient } from '../utils/apiClient'

export type ConsentAssurance =
  | 'standard'
  | 'push_approved'
  | 'biometric_confirmed'
  | 'bypassed_emergency'
  | 'standard_fallback_from_push'

export interface RoutineConsentRequest {
  patient_uuid: string
  hospital_id: string
  clinician_id: string
  purpose: string
  consent_assurance?: ConsentAssurance
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
  patient_uuid: string
  purpose: string
  consent_assurance: ConsentAssurance
  granted_at: string
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
      AxiosResponse<ConsentResponse>,
      RoutineConsentRequest
    >('/api/v2/consent/routine/issue', {
      ...payload,
      consent_assurance: payload.consent_assurance || 'standard',
    })
    return response.data
  } catch (error: unknown) {
    if (axios.isAxiosError(error)) {
      const status = error.response?.status
      throw new ConsentError(
        error.response?.data?.detail || 'Failed to issue consent',
        'CONSENT_FAILED',
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
      AxiosResponse<ConsentResponse>,
      BreakGlassRequest
    >('/api/v2/consent/break-glass/issue', payload)
    return response.data
  } catch (error: unknown) {
    if (axios.isAxiosError(error)) {
      throw new ConsentError(
        error.response?.data?.detail || 'Break-glass failed',
        'CONSENT_FAILED',
        error.response?.status
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
    const params = new URLSearchParams({ consent_token: consentToken })
    if (patientUuid) params.append('patient_uuid', patientUuid)

    const response = await apiClient.get<ConsentResponse>(
      `/api/v2/consent/validate?${params.toString()}`
    )
    return response.data
  } catch {
    return null
  }
}
