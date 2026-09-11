import { beforeEach, describe, expect, it, vi } from 'vitest'

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))

vi.mock('../utils/apiClient', () => ({
  ApiError: class ApiError extends Error {
    status = 0
    code: string | undefined
    isRetryable = false
    details: unknown = undefined
    constructor(
      message: string,
      status: number,
      code?: string,
      isRetryable = false,
      details?: unknown
    ) {
      super(message)
      this.status = status
      this.code = code
      this.isRetryable = isRetryable
      this.details = details
    }
  },
  apiClient: { get, post },
}))

import {
  RegistrationRecoveryClientError,
  completePatientRegistrationRecovery,
  getPatientRegistrationRecoveryReviewStatus,
  requestPatientRegistrationRecoveryOtp,
  verifyPatientRegistrationRecoveryOtp,
} from './patientRegistrationRecovery'

describe('patient registration recovery transport', () => {
  beforeEach(() => {
    post.mockReset()
  })

  it('starts recovery without requiring an existing patient session', async () => {
    post.mockResolvedValueOnce({
      data: {
        message: 'If this identity is eligible for account recovery, an OTP will be sent.',
        registration_recovery_attempt_token: 'attempt-token',
      },
    })

    await requestPatientRegistrationRecoveryOtp('+918000000001')

    expect(post).toHaveBeenCalledWith(
      '/api/v2/auth/registration-recovery/otp/send',
      { phone: '+918000000001' },
      { noAuth: true }
    )
  })

  it('verifies recovery OTP without requiring an existing patient session', async () => {
    post.mockResolvedValueOnce({
      data: {
        registration_recovery_token: 'repair-token',
        operation: 'repair_patient_registration_account',
        repair_kind: 'restore_patient_record_anchor',
        expires_in_seconds: 300,
        expires_at: '2026-09-10T18:00:00+00:00',
      },
    })

    await verifyPatientRegistrationRecoveryOtp({
      phone: '+918000000001',
      otp: '123456',
      registrationRecoveryAttemptToken: 'attempt-token',
    })

    expect(post).toHaveBeenCalledWith(
      '/api/v2/auth/registration-recovery/otp/verify',
      {
        phone: '+918000000001',
        otp: '123456',
        registration_recovery_attempt_token: 'attempt-token',
      },
      { noAuth: true }
    )
  })

  it('completes account repair without requiring a pre-existing patient session', async () => {
    post.mockResolvedValueOnce({
      data: {
        access_token: 'access-token',
        token_type: 'bearer',
        expires_at: '2026-09-10T18:00:00+00:00',
        patient_id: '11111111-1111-4111-8111-111111111111',
        repair_kind: 'restore_patient_record_anchor',
        device_authority_state: 'bootstrap_enrollment',
        device_enrollment_token: 'enrollment-token',
        device_enrollment_expires_in_seconds: 300,
      },
    })

    await completePatientRegistrationRecovery('repair-token')

    expect(post).toHaveBeenCalledWith(
      '/api/v2/auth/registration-recovery/complete',
      { registration_recovery_token: 'repair-token' },
      { noAuth: true }
    )
  })

  it('captures case_reference and manual_review error kind when verify returns 409 manual review required', async () => {
    const error = new (await import('../utils/apiClient')).ApiError(
      'Manual review required',
      409,
      'REGISTRATION_RECOVERY_MANUAL_REVIEW_REQUIRED',
      false,
      {
        detail: {
          error_code: 'REGISTRATION_RECOVERY_MANUAL_REVIEW_REQUIRED',
          case_reference: 'RRC-TEST123456789012345678',
        },
      }
    )
    post.mockRejectedValueOnce(error)

    try {
      await verifyPatientRegistrationRecoveryOtp({
        phone: '+918000000001',
        otp: '123456',
        registrationRecoveryAttemptToken: 'attempt-token',
      })
      expect.unreachable('Should have thrown')
    } catch (err) {
      expect(err).toBeInstanceOf(RegistrationRecoveryClientError)
      const clientErr = err as RegistrationRecoveryClientError
      expect(clientErr.kind).toBe('manual_review')
      expect(clientErr.code).toBe('REGISTRATION_RECOVERY_MANUAL_REVIEW_REQUIRED')
      expect(clientErr.retryable).toBe(false)
      expect(clientErr.caseReference).toBe('RRC-TEST123456789012345678')
    }
  })

  it('queries patient review status with noAuth and validates the response', async () => {
    const mockCase = {
      case_reference: 'RRC-TEST123456789012345678',
      status: 'PENDING' as const,
      terminal: false,
      next_action: 'WAIT_FOR_REVIEW' as const,
      created_at: '2026-09-11T10:00:00+00:00',
      resolved_at: null,
    }
    get.mockResolvedValueOnce({ data: mockCase })

    const res = await getPatientRegistrationRecoveryReviewStatus('RRC-TEST123456789012345678')

    expect(get).toHaveBeenCalledWith(
      '/api/v2/auth/registration-recovery/review/cases/RRC-TEST123456789012345678',
      { noAuth: true }
    )
    expect(res).toEqual(mockCase)
  })

  it('maps review case not found (404) to not_found error kind with caseReference', async () => {
    const error = new (await import('../utils/apiClient')).ApiError(
      'Case not found',
      404,
      'REGISTRATION_RECOVERY_REVIEW_CASE_NOT_FOUND',
      false
    )
    get.mockRejectedValueOnce(error)

    await expect(
      getPatientRegistrationRecoveryReviewStatus('RRC-NOTFOUND1234567890123')
    ).rejects.toMatchObject({
      kind: 'not_found',
      code: 'REGISTRATION_RECOVERY_REVIEW_CASE_NOT_FOUND',
      retryable: false,
      caseReference: 'RRC-NOTFOUND1234567890123',
    })
  })

  it('maps review service unavailable (503) to retryable network error', async () => {
    const error = new (await import('../utils/apiClient')).ApiError(
      'Service unavailable',
      503,
      'REGISTRATION_RECOVERY_REVIEW_UNAVAILABLE',
      true
    )
    get.mockRejectedValueOnce(error)

    await expect(
      getPatientRegistrationRecoveryReviewStatus('RRC-UNAVAIL1234567890123')
    ).rejects.toMatchObject({
      kind: 'network',
      retryable: true,
      caseReference: 'RRC-UNAVAIL1234567890123',
    })
  })
})

