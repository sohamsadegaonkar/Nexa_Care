import type { AxiosRequestConfig } from 'axios'
import { apiClient } from '../utils/api'

export interface PatientPolicyResponse {
  patient_uuid: string
  consent_assurance_policy: string
}

export async function getPatientPolicy(patientUuid: string): Promise<PatientPolicyResponse> {
  const res = await apiClient.get<PatientPolicyResponse>(`/api/v2/patient/${patientUuid}/policy`)
  return res.data
}

export async function updatePatientPolicy(
  patientUuid: string,
  policy: string,
  config?: AxiosRequestConfig
): Promise<PatientPolicyResponse> {
  const res = await apiClient.put<PatientPolicyResponse>(
    `/api/v2/patient/${patientUuid}/policy`,
    { consent_assurance_policy: policy },
    config
  )
  return res.data
}