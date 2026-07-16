import { beforeEach, describe, expect, it, vi } from 'vitest'

const { post } = vi.hoisted(() => ({ post: vi.fn() }))

vi.mock('../utils/apiClient', async (importOriginal) => {
  const original = await importOriginal<typeof import('../utils/apiClient')>()
  return { ...original, apiClient: { post } }
})

import {
  normalizePatientPhone,
  patientAuthError,
  requestPatientOtp,
  tryBeginPatientOtpSubmission,
  verifyPatientOtp,
} from './patientOtp'
import { ApiError } from '../utils/apiClient'

describe('patient OTP service', () => {
  beforeEach(() => post.mockReset())

  it('normalizes an Indian national number and requests the authoritative endpoint', async () => {
    post.mockResolvedValue({ data: { message: 'sent' } })
    await expect(requestPatientOtp('98765 43210')).resolves.toBe('+919876543210')
    expect(post).toHaveBeenCalledWith(
      '/api/v2/auth/otp/send',
      { phone: '+919876543210' },
      { noAuth: true }
    )
  })

  it('uses the exact verify endpoint and payload', async () => {
    const response = { access_token: 'access', device_enrollment_token: 'enroll' }
    post.mockResolvedValue({ data: response })
    await expect(verifyPatientOtp('+919876543210', '654321')).resolves.toBe(response)
    expect(post).toHaveBeenCalledWith(
      '/api/v2/auth/otp/verify',
      { phone: '+919876543210', otp: '654321' },
      { noAuth: true }
    )
  })

  it('blocks a duplicate submission until the current one releases the guard', () => {
    const guard = { current: false }
    expect(tryBeginPatientOtpSubmission(guard)).toBe(true)
    expect(tryBeginPatientOtpSubmission(guard)).toBe(false)
    guard.current = false
    expect(tryBeginPatientOtpSubmission(guard)).toBe(true)
  })

  it('keeps unsupported input unchanged for the backend validator', () => {
    expect(normalizePatientPhone('123')).toBe('123')
  })

  it.each([
    [
      new ApiError('Enter a valid Indian mobile number.', 422, 'VALIDATION_ERROR'),
      'Enter a valid Indian mobile number.',
    ],
    [
      new ApiError('Too many OTP requests. Try again in 60 seconds.', 429, 'RATE_LIMITED'),
      'Try again in 60 seconds',
    ],
    [new ApiError('transport', 0, 'NETWORK_ERROR'), 'Unable to reach Nexa Care'],
    [new ApiError('timeout', 0, 'REQUEST_TIMEOUT'), 'took too long'],
  ])('maps OTP errors without misclassifying them', (error, expected) => {
    expect(patientAuthError(error, 'fallback')).toContain(expected)
  })
})
