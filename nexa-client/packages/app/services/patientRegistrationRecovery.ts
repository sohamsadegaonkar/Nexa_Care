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

export type RegistrationRecoveryReviewStatus =
  | 'PENDING'
  | 'IN_REVIEW'
  | 'RESOLVED'
  | 'REJECTED'
  | 'SECURITY_ESCALATED'

export type RegistrationRecoveryReviewNextAction =
  | 'WAIT_FOR_REVIEW'
  | 'RESTART_ACCOUNT_RECOVERY'
  | 'CONTACT_SUPPORT'

export interface RegistrationRecoveryReviewStatusResponse {
  case_reference: string
  status: RegistrationRecoveryReviewStatus
  terminal: boolean
  next_action: RegistrationRecoveryReviewNextAction
  created_at: string
  resolved_at: string | null
}

export type RegistrationRecoveryClientErrorKind =
  | 'manual_review'
  | 'not_required'
  | 'not_available'
  | 'state_changed'
  | 'sign_in_required'
  | 'invalid_otp'
  | 'expired_attempt'
  | 'rate_limited'
  | 'network'
  | 'not_found'
  | 'unknown'

export class RegistrationRecoveryClientError extends Error {
  constructor(
    message: string,
    public readonly kind: RegistrationRecoveryClientErrorKind,
    public readonly code: string | undefined,
    public readonly retryable: boolean,
    public readonly caseReference?: string
  ) {
    super(message)
    this.name = 'RegistrationRecoveryClientError'
  }
}

function extractCaseReference(error: unknown): string | undefined {
  if (!error || typeof error !== 'object') return undefined
  const direct =
    (error as Record<string, unknown>).case_reference ||
    (error as Record<string, unknown>).caseReference
  if (typeof direct === 'string' && direct.trim()) return direct.trim()

  const details = (error as { details?: unknown }).details
  if (details && typeof details === 'object') {
    const rec = details as Record<string, unknown>
    if (typeof rec.case_reference === 'string' && rec.case_reference.trim()) {
      return rec.case_reference.trim()
    }
    const inner = rec.detail as Record<string, unknown> | undefined
    if (
      inner &&
      typeof inner === 'object' &&
      typeof inner.case_reference === 'string' &&
      inner.case_reference.trim()
    ) {
      return inner.case_reference.trim()
    }
  }
  return undefined
}

function mapRecoveryError(
  error: unknown,
  defaultCaseReference?: string
): RegistrationRecoveryClientError {
  if (error instanceof RegistrationRecoveryClientError) return error
  const caseReference = extractCaseReference(error) ?? defaultCaseReference

  if (error instanceof ApiError) {
    const code = error.code
    if (code === 'REGISTRATION_RECOVERY_MANUAL_REVIEW_REQUIRED') {
      return new RegistrationRecoveryClientError(
        'This account needs manual review before it can be repaired.',
        'manual_review',
        code,
        false,
        caseReference
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
    if (
      code === 'REGISTRATION_RECOVERY_STATE_CHANGED' ||
      code === 'REGISTRATION_RECOVERY_RESTART_REQUIRED'
    ) {
      return new RegistrationRecoveryClientError(
        'The account could not be repaired with this one-time authority. Restart account recovery.',
        'state_changed',
        code,
        false
      )
    }
    if (code === 'PATIENT_SESSION_AUTHORITY_UNAVAILABLE') {
      return new RegistrationRecoveryClientError(
        'The account repair completed, but a patient session could not be established. Sign in normally with a fresh OTP.',
        'sign_in_required',
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
    if (
      code === 'REGISTRATION_RECOVERY_REVIEW_CASE_NOT_FOUND' ||
      error.status === 404
    ) {
      return new RegistrationRecoveryClientError(
        'The requested review case could not be found.',
        'not_found',
        code ?? 'REGISTRATION_RECOVERY_REVIEW_CASE_NOT_FOUND',
        false,
        caseReference
      )
    }
    if (
      code === 'REGISTRATION_RECOVERY_REVIEW_UNAVAILABLE' ||
      error.status === 0 ||
      error.isRetryable
    ) {
      return new RegistrationRecoveryClientError(
        'Nexa Care could not complete account recovery right now. Check your connection and retry.',
        'network',
        code,
        true,
        caseReference
      )
    }
    return new RegistrationRecoveryClientError(
      error.message || 'Account recovery was rejected.',
      'unknown',
      code,
      false,
      caseReference
    )
  }
  return new RegistrationRecoveryClientError(
    error instanceof Error ? error.message : 'Account recovery failed.',
    'unknown',
    undefined,
    false,
    caseReference
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

export async function getPatientRegistrationRecoveryReviewStatus(
  caseReference: string
): Promise<RegistrationRecoveryReviewStatusResponse> {
  const normalizedCaseRef = caseReference?.trim()
  if (!normalizedCaseRef) {
    throw new RegistrationRecoveryClientError(
      'A case reference is required to check review status.',
      'unknown',
      'CASE_REFERENCE_REQUIRED',
      false
    )
  }

  try {
    const { data } =
      await apiClient.get<RegistrationRecoveryReviewStatusResponse>(
        `/api/v2/auth/registration-recovery/review/cases/${encodeURIComponent(normalizedCaseRef)}`,
        { noAuth: true }
      )

    if (
      !data ||
      typeof data !== 'object' ||
      typeof data.case_reference !== 'string' ||
      typeof data.status !== 'string' ||
      typeof data.terminal !== 'boolean' ||
      typeof data.next_action !== 'string' ||
      typeof data.created_at !== 'string'
    ) {
      throw new RegistrationRecoveryClientError(
        'The review status response was malformed.',
        'unknown',
        'MALFORMED_REVIEW_STATUS_RESPONSE',
        true,
        normalizedCaseRef
      )
    }

    return data
  } catch (error) {
    throw mapRecoveryError(error, normalizedCaseRef)
  }
}

