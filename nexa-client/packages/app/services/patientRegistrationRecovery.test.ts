import { beforeEach, describe, expect, it, vi } from 'vitest'

const { post } = vi.hoisted(() => ({ post: vi.fn() }))

vi.mock('../utils/apiClient', () => ({
  ApiError: class ApiError extends Error {
    status = 0
    code: string | undefined
    isRetryable = false
  },
  apiClient: { post },
}))

import {
  completePatientRegistrationRecovery,
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
})
