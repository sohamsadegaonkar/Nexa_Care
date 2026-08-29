import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  ensure: vi.fn(),
  biometrics: vi.fn(),
  sign: vi.fn(),
  approve: vi.fn(),
  deny: vi.fn(),
}))

vi.mock('./currentDeviceEnrollment', () => ({
  ensureCurrentDeviceEnrollment: mocks.ensure,
  CurrentDeviceError: class CurrentDeviceError extends Error {
    code = 'SETUP_REQUIRED'
  },
}))
vi.mock('./deviceKeys', () => ({
  authenticateWithBiometrics: mocks.biometrics,
  constructConsentSigningInput: vi.fn(() => 'canonical-input'),
  signConsentChallenge: mocks.sign,
}))
vi.mock('../utils/apiClient', async (importOriginal) => {
  const original = await importOriginal<typeof import('../utils/apiClient')>()
  return {
    ...original,
    NexaApiClient: {
      approveSignedConsent: mocks.approve,
      denySignedConsent: mocks.deny,
      fetchConsentChallenge: vi.fn(),
    },
  }
})

import { ApiError } from '../utils/apiClient'
import { approveWithBiometric, classifyConsentError } from './consentSigning'

const challenge = {
  protocol_version: 'nexa-consent-v2' as const,
  request_id: 'request-1',
  patient_id: 'patient-1',
  provider_id: 'provider-1',
  provider_name: 'Provider',
  hospital_name: 'Hospital',
  purpose: 'routine_checkup',
  scope: 'clinical',
  access_duration: 900,
  challenge_nonce: 'nonce-1',
  issued_at: '2098-12-31T23:45:00Z',
  expires_at: '2099-01-01T00:00:00Z',
  status: 'pending',
}

describe('current-device signed approval', () => {
  beforeEach(() => {
    mocks.ensure.mockReset().mockResolvedValue({
      deviceId: 'current-device',
      status: 'active',
      enrolledNow: false,
      keyFingerprint: 'fingerprint',
    })
    mocks.biometrics.mockReset().mockResolvedValue(undefined)
    mocks.sign.mockReset().mockResolvedValue('ecdsa-signature')
    mocks.approve.mockReset().mockResolvedValue({
      request_id: 'request-1',
      status: 'approved',
      responded_at: 'now',
    })
  })

  it('confirms exact enrollment before biometrics and signing', async () => {
    await approveWithBiometric(challenge)
    expect(mocks.ensure).toHaveBeenCalledWith({ allowEnrollment: false })
    expect(mocks.ensure.mock.invocationCallOrder[0]).toBeLessThan(
      mocks.biometrics.mock.invocationCallOrder[0]!
    )
    expect(mocks.biometrics.mock.invocationCallOrder[0]).toBeLessThan(
      mocks.sign.mock.invocationCallOrder[0]!
    )
  })

  it('submits the signature with the exact current installation device_id', async () => {
    await approveWithBiometric(challenge)
    expect(mocks.approve).toHaveBeenCalledWith(
      expect.objectContaining({
        request_id: 'request-1',
        patient_id: 'patient-1',
        decision: 'approved',
        challenge_nonce: 'nonce-1',
        signature: 'ecdsa-signature',
        device_id: 'current-device',
      })
    )
  })

  it('never invokes biometrics, signing, or submission when setup is required', async () => {
    mocks.ensure.mockRejectedValue(new Error('Secure this device to approve consent requests.'))
    await expect(approveWithBiometric(challenge)).rejects.toThrow('Secure this device')
    expect(mocks.biometrics).not.toHaveBeenCalled()
    expect(mocks.sign).not.toHaveBeenCalled()
    expect(mocks.approve).not.toHaveBeenCalled()
  })

  it('never classifies an HTTP 401 as an expired consent request', () => {
    expect(classifyConsentError(new ApiError('unauthorized', 401, 'REAUTH_REQUIRED'))).toEqual({
      kind: 'reauth',
      message: 'Your session expired. Sign in again.',
    })
  })
})
