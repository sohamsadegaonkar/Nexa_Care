import { ApiError, apiClient } from '../utils/apiClient'

export interface PatientOtpVerifyResponse {
  access_token: string
  token_type: 'bearer'
  expires_at: string
  patient_id: string
  device_enrollment_token: string
}

export interface SubmissionGuard {
  current: boolean
}

export function tryBeginPatientOtpSubmission(guard: SubmissionGuard): boolean {
  if (guard.current) return false
  guard.current = true
  return true
}

export function normalizePatientPhone(value: string): string {
  const digits = value.replace(/\D/g, '')
  if (digits.length === 10) return `+91${digits}`
  if (digits.length === 12 && digits.startsWith('91')) return `+${digits}`
  return value.trim()
}

export async function requestPatientOtp(phone: string): Promise<string> {
  const normalizedPhone = normalizePatientPhone(phone)
  await apiClient.post('/api/v2/auth/otp/send', { phone: normalizedPhone }, { noAuth: true })
  return normalizedPhone
}

export async function verifyPatientOtp(
  phone: string,
  otp: string
): Promise<PatientOtpVerifyResponse> {
  const { data } = await apiClient.post<PatientOtpVerifyResponse>(
    '/api/v2/auth/otp/verify',
    { phone, otp },
    { noAuth: true }
  )
  return data
}

export function patientAuthError(error: unknown, fallback: string): string {
  if (!(error instanceof ApiError)) return fallback
  if (error.code === 'MISSING_API_BASE_URL' || error.code === 'INVALID_API_BASE_URL') {
    return 'This app build is missing a valid server configuration. Install a correctly configured build.'
  }
  if (error.code === 'INSECURE_API_URL') return error.message
  if (error.code === 'REQUEST_TIMEOUT')
    return 'The server took too long to respond. Please try again.'
  if (error.code === 'NETWORK_ERROR') {
    return 'Unable to reach Nexa Care. Check that the configured server is available and try again.'
  }
  if (error.status === 400 || error.status === 422) return error.message
  if (error.status === 401)
    return 'The OTP is invalid or expired. Request a new code and try again.'
  if (error.status === 403) return 'No patient account is linked to this verified phone number.'
  if (error.status === 429) return error.message
  if (error.status >= 500)
    return 'The Nexa Care service is temporarily unavailable. Please try again later.'
  return error.message || fallback
}
