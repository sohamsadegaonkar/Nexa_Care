import { apiClient } from '../utils/api'

export interface PushApprovalRequest {
  patient_id: string
  provider_id: string
  purpose: string
  scope: string
}

export interface PushApprovalResponse {
  request_id: string
}

export async function requestPushApproval(
  payload: PushApprovalRequest
): Promise<PushApprovalResponse> {
  const res = await apiClient.post<PushApprovalResponse>(
    '/api/v2/push/request',
    payload
  )
  return res.data
}

export interface BiometricVerifyRequest {
  patient_uuid: string
  biometric_token: string
}

export interface PushApprovalStatusResponse {
  request_id: string
  patient_id: string
  clinician_name: string
  hospital_name: string
  purpose: string
  status: 'pending' | 'approved' | 'denied' | 'expired'
  created_at: string
  nonce: string
}

export interface PushApprovalRespondRequest {
  decision: 'approved' | 'denied'
  signature?: string
  nonce?: string
}

export async function getPushRequestStatus(
  requestId: string
): Promise<PushApprovalStatusResponse> {
  const res = await apiClient.get<PushApprovalStatusResponse>(
    `/api/v2/push/${requestId}/status`
  )
  return res.data
}

export async function respondToPushRequest(
  requestId: string,
  payload: PushApprovalRespondRequest
): Promise<{ status: string }> {
  const res = await apiClient.post(`/api/v2/push/${requestId}/respond`, payload)
  return res.data
}

export interface DeviceKeyRegistrationRequest {
  public_key: string
}

export async function registerDeviceKey(payload: DeviceKeyRegistrationRequest): Promise<void> {
  await apiClient.post('/api/v2/push/register-device-key', payload)
}
