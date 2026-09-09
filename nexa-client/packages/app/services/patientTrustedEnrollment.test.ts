import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  post: vi.fn(),
  sign: vi.fn(),
  current: {
    alias: 'current-native',
    publicKeyDerBase64: 'authorizer-der',
    custody: 'android-keystore',
    platform: 'android',
    nonExportable: true,
  },
}))

vi.mock('../utils/apiClient', () => ({ apiClient: { post: mocks.post } }))
vi.mock('./nativeDeviceKeyring', () => ({
  getCurrentNativeDeviceKey: vi.fn(async () => mocks.current),
  fingerprintPublicKeyDerBase64: vi.fn(async (value: string) =>
    value === 'authorizer-der' ? 'authorizer-fp' : 'new-fp'
  ),
}))
vi.mock('./nativeDeviceSecurity', () => ({ signWithNativeDeviceKey: mocks.sign }))

const authorizer = {
  device_id: 'device-1',
  key_id: 'key-1',
  key_version: 3,
  device_label: 'Pixel',
  platform: 'android',
  status: 'active',
  enrolled_at: '2026-09-09T00:00:00Z',
  public_key_fingerprint: 'authorizer-fp',
}

function challenge(overrides: Record<string, unknown> = {}) {
  const body = {
    protocol_version: 'nexa-trusted-device-enrollment-v1',
    operation: 'authorize_trusted_device_enrollment',
    authorizer_device_id: 'device-1',
    authorizer_key_version: 3,
    new_public_key_fingerprint: 'new-fp',
    challenge_nonce: 'nonce-1',
    issued_at: '2026-09-09T00:00:00Z',
    expires_at: '2026-09-09T00:05:00Z',
    ...overrides,
  }
  return {
    ...body,
    signing_payload_b64: Buffer.from(JSON.stringify(body), 'utf8').toString('base64'),
  }
}

describe('native trusted-device authorization', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.sign.mockResolvedValue('native-signature')
    mocks.post
      .mockResolvedValueOnce({ data: challenge() })
      .mockResolvedValueOnce({
        data: {
          device_id: 'device-2',
          key_id: 'key-2',
          key_version: 1,
          status: 'active',
          patient_id: 'patient-1',
          enrolled_at: '2026-09-09T00:01:00Z',
        },
      })
  })

  it('signs the exact bound challenge with the current native alias', async () => {
    const { authorizeProspectiveTrustedDevice } = await import('./patientTrustedEnrollment')
    await expect(
      authorizeProspectiveTrustedDevice({
        authorizer,
        newPublicKeyDerBase64: 'new-device-der',
        deviceLabel: 'New iPhone',
        platform: 'ios',
      })
    ).resolves.toMatchObject({ device_id: 'device-2', key_version: 1 })
    expect(mocks.sign).toHaveBeenCalledWith(
      'current-native',
      expect.stringContaining('authorize_trusted_device_enrollment')
    )
    expect(JSON.stringify(mocks.post.mock.calls[1]?.[1])).not.toContain('private')
  })

  it('rejects a challenge substituted to a different authorizer key version', async () => {
    mocks.post.mockReset().mockResolvedValueOnce({
      data: challenge({ authorizer_key_version: 4 }),
    })
    const { authorizeProspectiveTrustedDevice } = await import('./patientTrustedEnrollment')
    await expect(
      authorizeProspectiveTrustedDevice({
        authorizer,
        newPublicKeyDerBase64: 'new-device-der',
        deviceLabel: 'New iPhone',
        platform: 'ios',
      })
    ).rejects.toThrow('TRUSTED_ENROLLMENT_CHALLENGE_BINDING_MISMATCH')
    expect(mocks.sign).not.toHaveBeenCalled()
  })
})
