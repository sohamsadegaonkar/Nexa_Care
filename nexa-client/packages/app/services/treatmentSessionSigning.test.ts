import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { TreatmentSessionV1Challenge } from '../utils/apiClient'

const mocks = vi.hoisted(() => ({
  submit: vi.fn(),
  biometrics: vi.fn(),
  enrollment: vi.fn(),
  sign: vi.fn(),
}))

vi.mock('../utils/apiClient', async () => {
  const original = await vi.importActual<any>('../utils/apiClient')
  return {
    ...original,
    NexaApiClient: {
      ...original.NexaApiClient,
      submitSignedTreatmentSessionV1: mocks.submit,
      fetchTreatmentSessionV1Challenge: vi.fn(),
    },
  }
})
vi.mock('./deviceKeys', () => ({
  authenticateWithBiometrics: mocks.biometrics,
}))
vi.mock('./currentDeviceEnrollment', async () => {
  class CurrentDeviceError extends Error {
    constructor(
      message: string,
      public readonly code: string
    ) {
      super(message)
    }
  }
  return {
    CurrentDeviceError,
    ensureCurrentDeviceEnrollment: mocks.enrollment,
  }
})
vi.mock('./nativeDeviceSecurity', () => ({
  signWithNativeDeviceKey: mocks.sign,
}))

import {
  approveTreatmentSessionWithBiometric,
  constructTreatmentSessionSigningInput,
  denyTreatmentSessionWithSignature,
} from './treatmentSessionSigning'

const challenge: TreatmentSessionV1Challenge = {
  protocol_version: 'nexa-treatment-session-v1',
  request_id: '11111111-1111-4111-8111-111111111111',
  patient_id: '22222222-2222-4222-8222-222222222222',
  provider_id: '33333333-3333-4333-8333-333333333333',
  hospital_id: '44444444-4444-4444-8444-444444444444',
  provider_name: 'Synthetic Provider',
  hospital_name: 'Synthetic Hospital',
  provider_session_binding_hash: 'b'.repeat(64),
  purpose: 'record_vitals',
  allowed_operations: ['CREATE_ENCOUNTER', 'WRITE_VITALS'],
  access_duration: 900,
  challenge_nonce: 'synthetic-treatment-nonce',
  issued_at: '2026-09-19T09:00:00+00:00',
  expires_at: '2099-09-19T09:02:00+00:00',
  treatment_context_hash: 'c'.repeat(64),
  status: 'pending',
}

const device = {
  deviceId: '55555555-5555-4555-8555-555555555555',
  keyId: '66666666-6666-4666-8666-666666666666',
  keyVersion: 2,
  status: 'active' as const,
  enrolledNow: false,
  keyFingerprint: 'd'.repeat(64),
  keyAlias: 'native-key-alias',
  custody: 'android-keystore-hardware' as const,
}

describe('Treatment Session V1 signing', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    mocks.enrollment.mockResolvedValue(device)
    mocks.biometrics.mockResolvedValue(undefined)
    mocks.sign.mockResolvedValue('synthetic-signature')
    mocks.submit.mockResolvedValue({
      protocol_version: 'nexa-treatment-session-v1',
      request_id: challenge.request_id,
      status: 'approved',
      responded_at: '2026-09-19T09:01:00+00:00',
    })
  })

  it('constructs the exact domain-separated operation-bound bytes', () => {
    const input = constructTreatmentSessionSigningInput(challenge, 'approved', device)
    expect(input).toBe(
      JSON.stringify({
        access_duration: 900,
        allowed_operations: ['CREATE_ENCOUNTER', 'WRITE_VITALS'],
        challenge_nonce: 'synthetic-treatment-nonce',
        decision: 'approved',
        device_id: device.deviceId,
        domain: 'NEXA_CARE_SIGNED_TREATMENT_SESSION',
        expires_at: challenge.expires_at,
        hospital_id: challenge.hospital_id,
        issued_at: challenge.issued_at,
        key_id: device.keyId,
        key_version: 2,
        operation: 'TREATMENT_SESSION_DECISION',
        patient_id: challenge.patient_id,
        policy_version: 'clinical-access-v1',
        protocol_version: 'nexa-treatment-session-v1',
        provider_id: challenge.provider_id,
        provider_session_binding_hash: challenge.provider_session_binding_hash,
        public_key_fingerprint: device.keyFingerprint,
        purpose: 'record_vitals',
        request_id: challenge.request_id,
        treatment_context_hash: challenge.treatment_context_hash,
      })
    )
  })

  it('rejects operation widening before native signing', () => {
    expect(() =>
      constructTreatmentSessionSigningInput(
        {
          ...challenge,
          allowed_operations: [
            'CREATE_ENCOUNTER',
            'WRITE_VITALS',
            'WRITE_PRESCRIPTION' as any,
          ],
        },
        'approved',
        device
      )
    ).toThrow('TREATMENT_OPERATION_SET_UNSUPPORTED')
    expect(mocks.sign).not.toHaveBeenCalled()
  })

  it('uses biometrics for approval and submits only the signed decision envelope', async () => {
    await approveTreatmentSessionWithBiometric(challenge)
    expect(mocks.biometrics).toHaveBeenCalledOnce()
    expect(mocks.sign).toHaveBeenCalledOnce()
    expect(mocks.submit).toHaveBeenCalledWith(
      expect.objectContaining({
        protocol_version: 'nexa-treatment-session-v1',
        request_id: challenge.request_id,
        decision: 'approved',
        signature: 'synthetic-signature',
      })
    )
    expect(JSON.stringify(mocks.submit.mock.calls[0]?.[0])).not.toContain(
      'provider_session_binding_hash'
    )
  })

  it('signs denial without a biometric approval step', async () => {
    mocks.submit.mockResolvedValueOnce({
      protocol_version: 'nexa-treatment-session-v1',
      request_id: challenge.request_id,
      status: 'denied',
      responded_at: '2026-09-19T09:01:00+00:00',
    })
    await denyTreatmentSessionWithSignature(challenge)
    expect(mocks.biometrics).not.toHaveBeenCalled()
    expect(mocks.sign).toHaveBeenCalledOnce()
  })
})
