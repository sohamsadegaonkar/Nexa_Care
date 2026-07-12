
import { apiClient, ApiError, type ApiResponse } from '../utils/apiClient'

export interface PatientDemographics {
  patient_name?: string
  name?: string
  age?: number | string
  blood_type?: string
  bloodType?: string
  phone?: string
  contact_info?: string
  contactInfo?: string
}

export interface PatientClinicalData {
  medications?: string[]
  prescriptions?: string[]
  allergies?: string[]
  diagnoses?: string[]
  recent_diagnoses?: string[]
  recentDiagnoses?: string[]
  clinical_notes?: string[] | string
  clinicalNotes?: string[] | string
}

export interface PatientRecordResponse {
  pii?: PatientDemographics
  clinical?: PatientClinicalData
  demographics?: PatientDemographics
  medications?: string[]
  allergies?: string[]
  clinical_notes?: string[] | string
}

export type PatientRecordErrorCode =
  | 'PATIENT_RECORD_CONSENT_INVALID'
  | 'PATIENT_RECORD_NOT_FOUND'
  | 'PATIENT_RECORD_UNAVAILABLE'
  | 'PATIENT_RECORD_FAILED'

export class PatientRecordError extends Error {
  public readonly code: PatientRecordErrorCode
  public readonly status?: number
  public readonly retryable: boolean

  public constructor(
    message: string,
    code: PatientRecordErrorCode,
    retryable: boolean,
    status?: number
  ) {
    super(message)
    this.name = 'PatientRecordError'
    this.code = code
    this.status = status
    this.retryable = retryable
  }
}

/**
 * Fetches a consent-scoped reconstructed patient record.
 */
export async function fetchPatientRecord(
  patientId: string,
  consentToken: string,
  purpose: string
): Promise<PatientRecordResponse> {
  const normalizedPatientId = patientId.trim()
  const normalizedConsentToken = consentToken.trim()
  const normalizedPurpose = purpose.trim()

  if (!normalizedPatientId || !normalizedConsentToken || !normalizedPurpose) {
    throw new PatientRecordError(
      'Consent token, purpose, and patient ID are required.',
      'PATIENT_RECORD_CONSENT_INVALID',
      false
    )
  }

  try {
    const response = await apiClient.get<
      PatientRecordResponse,
      ApiResponse<PatientRecordResponse>
    >(`/api/v2/patient/${encodeURIComponent(normalizedPatientId)}/record`, {
      headers: {
        'X-Consent-Token': normalizedConsentToken,
        'X-Consent-Purpose': normalizedPurpose,
      },
    })

    return response.data
  } catch (error: unknown) {
    if (error instanceof ApiError) {
      const status = error.status

      if (status === 401 || status === 403) {
        throw new PatientRecordError(
          'Consent Expired or Invalid',
          'PATIENT_RECORD_CONSENT_INVALID',
          false,
          status
        )
      }

      if (status === 404) {
        throw new PatientRecordError(
          'Patient record was not found.',
          'PATIENT_RECORD_NOT_FOUND',
          false,
          status
        )
      }

      if (status === 503) {
        throw new PatientRecordError(
          'Patient record service is temporarily unavailable.',
          'PATIENT_RECORD_UNAVAILABLE',
          true,
          status
        )
      }
    }

    throw new PatientRecordError('Unable to fetch patient record.', 'PATIENT_RECORD_FAILED', false)
  }
}
