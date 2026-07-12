import { apiClient } from '../utils/apiClient'

/** @deprecated Name retained for callers; transport is the canonical consent API. */
export interface PushApprovalRequest {
  patient_id: string
  provider_id: string
  purpose: string
  scope: string
}
export interface PushApprovalResponse { request_id: string }
export async function requestPushApproval(payload: PushApprovalRequest): Promise<PushApprovalResponse> {
  const { data } = await apiClient.post<PushApprovalResponse>('/api/v2/consent/request', payload)
  return data
}

export interface PushApprovalStatusResponse {
  request_id: string
  status: 'pending' | 'approved' | 'denied' | 'expired' | 'timeout'
}
export async function getPushRequestStatus(requestId: string): Promise<PushApprovalStatusResponse> {
  const { data } = await apiClient.get<PushApprovalStatusResponse>(`/api/v2/consent/status/${requestId}`)
  return data
}

/** @deprecated Device enrollment belongs to services/deviceKeys.ts. */
export async function registerDeviceKey(): Promise<never> {
  throw new Error('Use the canonical deviceKeys enrollment service.')
}
