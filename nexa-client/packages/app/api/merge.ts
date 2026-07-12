
import { apiClient, ApiError, type ApiResponse } from '../utils/apiClient'

export interface PatientMergeRequest {
  old_patient_uuid: string
  canonical_patient_uuid: string
  reason: string
  evidence?: Record<string, any>
}

export interface PatientMergeResponse {
  message: string
  tombstone_id: string
  canonical_patient_uuid: string
}

export type MergeErrorCode =
  | 'MERGE_INVALID_REQUEST'
  | 'MERGE_UNAUTHORIZED'
  | 'MERGE_FAILED'

export class MergeError extends Error {
  public readonly code: MergeErrorCode
  public readonly status?: number

  constructor(message: string, code: MergeErrorCode, status?: number) {
    super(message)
    this.name = 'MergeError'
    this.code = code
    this.status = status
  }
}

/**
 * Performs a supervised patient merge (creates tombstone).
 */
export async function mergePatients(
  payload: PatientMergeRequest,
  challengeToken: string
): Promise<PatientMergeResponse> {
  try {
    const response = await apiClient.post<
      PatientMergeResponse,
      ApiResponse<PatientMergeResponse>,
      PatientMergeRequest
    >('/api/v2/patient/merge', payload, {
      headers: {
        'X-Merge-Challenge': challengeToken,
      },
    })

    return response.data
  } catch (error: unknown) {
    if (error instanceof ApiError) {
      const status = error.status
      throw new MergeError(
        error.message || 'Merge failed',
        'MERGE_FAILED',
        status
      )
    }
    throw new MergeError('Unable to perform merge', 'MERGE_FAILED')
  }
}
