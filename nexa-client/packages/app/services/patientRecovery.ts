import { apiClient } from '../utils/apiClient'

export interface RecoveryCapabilityResponse {
  recovery_token: string
  operation: 'recover_patient_device_authority'
  expires_in_seconds: number
  expires_at: string
}

export interface RecoveryCompleteResponse {
  access_token: string
  token_type: 'bearer'
  expires_at: string
  patient_id: string
  device_id: string
  key_id: string
  key_version: number
  status: 'active'
  public_key_fingerprint: string
  revoked_device_count: number
}

export async function requestPatientRecoveryOtp(phone: string): Promise<void> {
  await apiClient.post('/api/v2/patient/devices/recovery/otp/send', { phone })
}

export async function verifyPatientRecoveryOtp(
  phone: string,
  otp: string
): Promise<RecoveryCapabilityResponse> {
  const { data } = await apiClient.post<RecoveryCapabilityResponse>(
    '/api/v2/patient/devices/recovery/otp/verify',
    { phone, otp }
  )
  return data
}

export async function completePatientDeviceRecovery(params: {
  recovery_token: string
  new_device_public_key: string
  device_label: string
  platform: 'ios' | 'android'
}): Promise<RecoveryCompleteResponse> {
  const { data } = await apiClient.post<RecoveryCompleteResponse>(
    '/api/v2/patient/devices/recovery/complete',
    params
  )
  return data
}
