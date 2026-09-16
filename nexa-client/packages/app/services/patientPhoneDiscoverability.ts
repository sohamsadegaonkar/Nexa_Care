import { apiClient } from '../utils/apiClient'

export interface PhoneDiscoverabilityState {
  enabled: boolean
}

export async function getPhoneDiscoverability(): Promise<PhoneDiscoverabilityState> {
  const { data } = await apiClient.get<PhoneDiscoverabilityState>(
    '/api/v2/patient/me/discoverability/phone'
  )
  return data
}

export async function enablePhoneDiscoverability(
  phone: string,
  otp: string
): Promise<PhoneDiscoverabilityState> {
  const { data } = await apiClient.post<PhoneDiscoverabilityState>(
    '/api/v2/patient/me/discoverability/phone/enable',
    { phone, otp }
  )
  return data
}

export async function disablePhoneDiscoverability(): Promise<PhoneDiscoverabilityState> {
  const { data } = await apiClient.delete<PhoneDiscoverabilityState>(
    '/api/v2/patient/me/discoverability/phone'
  )
  return data
}
