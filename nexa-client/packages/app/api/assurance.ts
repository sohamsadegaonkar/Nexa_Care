import { apiClient } from '../utils/api'

export interface PushApprovalRequest {
  patient_uuid: string
  clinician_name: string
  hospital_name: string
  purpose: string
}

export interface PushApprovalResponse {
  approved: boolean
  timeout: boolean
}

export async function requestPushApproval(
  payload: PushApprovalRequest
): Promise<PushApprovalResponse> {
  const res = await apiClient.post<PushApprovalResponse>(
    '/api/v2/assurance/push/request',
    payload
  )
  return res.data
}

export interface BiometricVerifyRequest {
  patient_uuid: string
  biometric_token: string
}

export async function verifyBiometric(
  payload: BiometricVerifyRequest
): Promise<{ verified: boolean }> {
  const res = await apiClient.post('/api/v2/assurance/biometric/verify', payload)
  return res.data
}
