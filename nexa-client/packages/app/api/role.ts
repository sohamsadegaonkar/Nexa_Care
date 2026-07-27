import { apiClient } from '../utils/apiClient'

export interface RoleResponse {
  role: 'receptionist' | 'clinician' | 'admin'
  provider_id: string
}

export async function getMyRole(): Promise<RoleResponse> {
  const res = await apiClient.get<RoleResponse>('/api/v2/auth/me/role')
  return res.data
}
