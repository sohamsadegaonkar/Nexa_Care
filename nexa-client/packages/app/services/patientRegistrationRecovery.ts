import { apiClient, ApiError } from '../utils/apiClient'

export type RegistrationRecoveryRepairKind =
  | 'restore_patient_record_anchor'
  | 'rebind_merged_identity'
  | 'rebind_merged_identity_and_restore_record_anchor'

export interface RegistrationRecoveryOtpSendResponse {
  message: string
  registration_recovery_attempt_token: string
}

export interface RegistrationRecoveryCapabilityResponse {
  registration_recovery_token: string
  operation: 'repair_patient_registration_account'
  repair_kind: RegistrationRecoveryRepairKind
  expires_in_seconds: number
  expires_at: string
}

export interface RegistrationRecoveryCompleteResponse {
  access_token: string
  token_type: 'bearer'
  expires_at: string
  patient_id: string
  repair_kind: RegistrationRecoveryRepairKind
  device_authority_state: 'bootstrap_enrollment' | 'existing_device_required'
  device_enrollment_token: string | null
  device_enrollment_expires_in_seconds: number | null
}

export type RegistrationRecoveryClientErrorKind =
  | 'manual_review'
  | 'not_required'
  | 'not_available'
  | 'state_changed'
  | 'invalid_otp'
  | 'expired_attempt'
  | 'rate_limited'
  | 'network'
  | 'unknown'

export class RegistrationRecoveryClientError extends Error {
  constructor(
    message: string,
    public readonly kind: RegistrationRecoveryClientErrorKind,
    public readonly code: string | undefined,
    public readonly retryable: boolean
  ) {
    super(message)
    this.name = 'RegistrationRecoveryClientError'
  }
}

function mapRecoveryError(error: unknown): RegistrationRecoveryClientError {
  if (error instanceof RegistrationRecoveryClientError) return error
  if (error instanceof ApiError) {
    const code = error.code
    if (code === 'REGISTRATION_RECOVERY_MANUAL_REVIEW_REQUIRED') {
      return new RegistrationRecoveryClientError(
        'This account needs manual review before it can be repaired.',
        'manual_review',
        code,
        false
      )
    }
    if (code === 'REGISTRATION_RECOVERY_NOT_REQUIRED') {
      return new RegistrationRecoveryClientError(
        'This account does not need registration repair. Sign in normally instead.',
        'not_required',
        code,
        false
      )
    }
    if (code === 'REGISTRATION_RECOVERY_NOT_AVAILABLE') {
      return new RegistrationRecoveryClientError(
        'No self-service registration repair is available for this verified identity.',
        'not_available',
        code,
        false
      )
    }
    if (code === 'REGISTRATION_RECOVERY_STATE_CHANGED') {
      return new RegistrationRecoveryClientError(
        'The account changed while recovery was in progress. Restart account recovery.',
        'state_changed',
        code,
        false
      )
    }
    if (code === 'REGISTRATION_RECOVERY_OTP_INVALID') {
      return new RegistrationRecoveryClientError(
        'That verification code is invalid.',
        'invalid_otp',
        code,
        false
      )
    }
    if (
      code === 'REGISTRATION_RECOVERY_ATTEMPT_INVALID' ||
      code === 'REGISTRATION_RECOVERY_ATTEMPT_EXHAUSTED'
    ) {
      return new RegistrationRecoveryClientError(
        'This recovery attempt is no longer valid. Request a new verification code.',
        'expired_attempt',
        code,
        false
      )
    }
    if (code === 'REGISTRATION_RECOVERY_OTP_RATE_LIMITED') {
      return new RegistrationRecoveryClientError(
        'Too many recovery requests. Try again later.',
        'rate_limited',
        code,
        false
      )
    }
    if (error.status === 0 || error.isRetryable) {
      return new RegistrationRecoveryClientError(
        'Nexa Care could not complete account recovery right now. Check your connection and retry.',
        'network',
        code,
        true
      )
    }
    return new RegistrationRecoveryClientError(
      error.message || 'Account recovery was rejected.',
      'unknown',
      code,
      false
    )
  }
  return new RegistrationRecoveryClientError(
    error instanceof Error ? error.message : 'Account recovery failed.',
    'unknown',
    undefined,
    false
  )
}

export async function requestPatientRegistrationRecoveryOtp(
  phone: string
): Promise<RegistrationRecoveryOtpSendResponse> {
  try {
    const { data } = await apiClient.post<RegistrationRecoveryOtpSendResponse>(
      '/api/v2/auth/registration-recovery/otp/send',
      { phone },
      { noAuth: true }
    )
    return data
  } catch (error) {
    throw mapRecoveryError(error)
  }
}

export async function verifyPatientRegistrationRecoveryOtp(params: {
  phone: string
  otp: string
  registrationRecoveryAttemptToken: string
}): Promise<RegistrationRecoveryCapabilityResponse> {
  try {
    const { data } = await apiClient.post<RegistrationRecoveryCapabilityResponse>(
      '/api/v2/auth/registration-recovery/otp/verify',
      {
        phone: params.phone,
        otp: params.otp,
        registration_recovery_attempt_token: params.registrationRecoveryAttemptToken,
      },
      { noAuth: true }
    )
    if (data.operation !== 'repair_patient_registration_account') {
      throw new RegistrationRecoveryClientError(
        'The recovery response did not contain account-repair authority.',
        'unknown',
        'REGISTRATION_RECOVERY_OPERATION_MISMATCH',
        false
      )
    }
    return data
  } catch (error) {
    throw mapRecoveryError(error)
  }
}

export async function completePatientRegistrationRecovery(
  registrationRecoveryToken: string
): Promise<RegistrationRecoveryCompleteResponse> {
  try {
    const { data } = await apiClient.post<RegistrationRecoveryCompleteResponse>(
      '/api/v2/auth/registration-recovery/complete',
      { registration_recovery_token: registrationRecoveryToken },
      { noAuth: true }
    )
    return data
  } catch (error) {
    throw mapRecoveryError(error)
  }
}
