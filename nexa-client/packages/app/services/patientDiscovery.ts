import { apiClient } from '../utils/apiClient'

export type DiscoveryIdentifierType = 'NEXA_PUBLIC_ID' | 'PHONE' | 'QR_PUBLIC_ID'

export interface PatientDiscoveryRequest {
  identifier_type: DiscoveryIdentifierType
  value: string
}

export interface PatientDiscoveryResponse {
  discovery_handle: string
  expires_at: string
}

/**
 * Exact provider discovery. The searched value is sent only in the POST body;
 * callers must never place it in a URL, navigation parameter, log, or storage.
 */
export async function discoverPatientExact(
  payload: PatientDiscoveryRequest,
  hospitalId: string
): Promise<PatientDiscoveryResponse> {
  const { data } = await apiClient.post<PatientDiscoveryResponse>(
    '/api/v2/patient-discovery',
    payload,
    { headers: { 'X-Hospital-Id': hospitalId } }
  )
  return data
}
