import axios, { type AxiosResponse } from 'axios'

import { apiClient } from '../utils/api'

export interface RoutineConsentIssueRequest {
  patient_id: string
  purpose: string
}

export interface RoutineConsentIssueResponse {
  consent_token: string
  expires_at?: string
}

export type RoutineConsentErrorCode =
  | 'ROUTINE_CONSENT_INVALID_REQUEST'
  | 'ROUTINE_CONSENT_UNAUTHORIZED'
  | 'ROUTINE_CONSENT_UNAVAILABLE'
  | 'ROUTINE_CONSENT_FAILED'

export class RoutineConsentError extends Error {
  public readonly code: RoutineConsentErrorCode
  public readonly status?: number
  public readonly retryable: boolean

  public constructor(
    message: string,
    code: RoutineConsentErrorCode,
    retryable: boolean,
    status?: number
  ) {
    super(message)
    this.name = 'RoutineConsentError'
    this.code = code
    this.status = status
    this.retryable = retryable
  }
}

/**
 * Requests a routine consent capability for a verified patient identity.
 */
export async function requestRoutineConsent(patientId: string, purpose: string): Promise<string> {
  const normalizedPatientId = patientId.trim()
  const normalizedPurpose = purpose.trim()

  if (!normalizedPatientId || !normalizedPurpose) {
    throw new RoutineConsentError(
      'Patient ID and access purpose are required.',
      'ROUTINE_CONSENT_INVALID_REQUEST',
      false
    )
  }

  try {
    const response = await apiClient.post<
      RoutineConsentIssueResponse,
      AxiosResponse<RoutineConsentIssueResponse>,
      RoutineConsentIssueRequest
    >('/api/v2/consent/routine/issue', {
      patient_id: normalizedPatientId,
      purpose: normalizedPurpose,
    })

    return response.data.consent_token
  } catch (error: unknown) {
    if (axios.isAxiosError(error)) {
      const status = error.response?.status

      if (status === 400 || status === 422) {
        throw new RoutineConsentError(
          'Consent request is missing required information.',
          'ROUTINE_CONSENT_INVALID_REQUEST',
          false,
          status
        )
      }

      if (status === 401 || status === 403) {
        throw new RoutineConsentError(
          'Provider session is not authorized to request consent.',
          'ROUTINE_CONSENT_UNAUTHORIZED',
          false,
          status
        )
      }

      if (status === 503) {
        throw new RoutineConsentError(
          'Routine consent service is temporarily unavailable.',
          'ROUTINE_CONSENT_UNAVAILABLE',
          true,
          status
        )
      }
    }

    throw new RoutineConsentError(
      'Unable to request routine consent.',
      'ROUTINE_CONSENT_FAILED',
      false
    )
  }
}

export interface BreakGlassConsentIssueRequest {
  patient_id: string
  reason_code: string
  free_text: string
}

export interface BreakGlassConsentIssueResponse {
  consent_token: string
  expires_at?: string
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
  reasonCode: string,
  freeText: string
): Promise<string> {
  const normalizedPatientId = patientId.trim()
  const normalizedReasonCode = reasonCode.trim()
  const normalizedFreeText = freeText.trim()

  if (!normalizedPatientId || !normalizedReasonCode || !normalizedFreeText) {
    throw new BreakGlassConsentError(
      'Patient ID, reason code, and justification are required.',
      'BREAK_GLASS_INVALID_REQUEST',
      false
    )
  }

  try {
    const response = await apiClient.post<
      BreakGlassConsentIssueResponse,
      AxiosResponse<BreakGlassConsentIssueResponse>,
      BreakGlassConsentIssueRequest
    >('/api/v2/consent/break-glass/issue', {
      patient_id: normalizedPatientId,
      reason_code: normalizedReasonCode,
      free_text: normalizedFreeText,
    })

    return response.data.consent_token
  } catch (error: unknown) {
    if (axios.isAxiosError(error)) {
      const status = error.response?.status

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
