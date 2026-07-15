import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  storage: new Map<string, string>(),
  secureStoreAvailable: true,
  accessToken: 'patient-access-token' as string | null,
  post: vi.fn(),
  setAuthTokenProvider: vi.fn(),
}))

const patientId = '123e4567-e89b-12d3-a456-426614174001'
const patientPayload = Buffer.from(JSON.stringify({
  sub: patientId,
  patient_id: patientId,
  exp: Math.floor(Date.now() / 1000) + 3600,
  jti: 'device-test-session',
})).toString('base64url')
const patientAccessToken = `header.${patientPayload}.signature`

vi.mock('expo-secure-store', () => ({
  WHEN_UNLOCKED_THIS_DEVICE_ONLY: 'WHEN_UNLOCKED_THIS_DEVICE_ONLY',
  isAvailableAsync: vi.fn(async () => mocks.secureStoreAvailable),
  getItemAsync: vi.fn(async (key: string) => mocks.storage.get(key) ?? null),
  setItemAsync: vi.fn(async (key: string, value: string) => {
    mocks.storage.set(key, value)
  }),
  deleteItemAsync: vi.fn(async (key: string) => {
    mocks.storage.delete(key)
  }),
}))

vi.mock('expo-crypto', () => ({
  CryptoDigestAlgorithm: { SHA256: 'SHA-256' },
  getRandomBytesAsync: vi.fn(async () => {
    const privateKey = new Uint8Array(32)
    privateKey[31] = 1
    return privateKey
  }),
  digest: vi.fn(async () => {
    const bytes = new Uint8Array(32)
    bytes[0] = 0xab
    return bytes.buffer
  }),
}))

vi.mock('expo-local-authentication', () => ({}))
vi.mock('react-native', () => ({ Platform: { OS: 'android' } }))
vi.mock('../utils/apiClient', () => ({
  apiClient: { post: mocks.post, get: vi.fn() },
  getAuthToken: vi.fn(async () => mocks.accessToken),
  setAuthTokenProvider: mocks.setAuthTokenProvider,
}))

import {
  DEVICE_ENROLLMENT_TOKEN_STORAGE_KEY,
  DEVICE_ID_STORAGE_KEY,
  DEVICE_PRIVATE_KEY_STORAGE_KEY,
  PATIENT_ACCESS_TOKEN_STORAGE_KEY,
  generateAndEnrollDevice,
  fingerprintDevicePublicKey,
  storePatientAuthSession,
} from './deviceKeys'

describe('physical-device enrollment prerequisites', () => {
  beforeEach(() => {
    mocks.storage.clear()
    mocks.secureStoreAvailable = true
    mocks.accessToken = patientAccessToken
    mocks.post.mockReset()
    mocks.setAuthTokenProvider.mockClear()
  })

  it('persists patient and enrollment tokens only in SecureStore', async () => {
    await storePatientAuthSession(patientAccessToken, 'enrollment-token-value')

    expect(mocks.storage.get(PATIENT_ACCESS_TOKEN_STORAGE_KEY)).toBe(patientAccessToken)
    expect(mocks.storage.get(DEVICE_ENROLLMENT_TOKEN_STORAGE_KEY)).toBe('enrollment-token-value')
    expect(mocks.setAuthTokenProvider).toHaveBeenCalledOnce()
  })

  it('fingerprints decoded DER bytes rather than the base64 text', async () => {
    await expect(fingerprintDevicePublicKey('AQID')).resolves.toBe(
      `ab${'00'.repeat(31)}`,
    )
  })

  it('uses Expo secure randomness, enrolls, and retains only device state', async () => {
    await storePatientAuthSession(patientAccessToken, 'enrollment-token-value')
    mocks.post.mockResolvedValue({
      data: {
        device_id: 'device-1',
        status: 'active',
        patient_id: 'patient-1',
        enrolled_at: '2026-07-14T00:00:00Z',
      },
    })
    const stages: string[] = []

    const result = await generateAndEnrollDevice('Test Android', (stage) => stages.push(stage))

    expect(result.device_id).toBe('device-1')
    expect(stages).toEqual(['generating', 'enrolling'])
    expect(mocks.post).toHaveBeenCalledWith(
      '/api/v2/patient/devices/enroll',
      expect.objectContaining({
        device_enrollment_token: 'enrollment-token-value',
        device_label: 'Test Android',
        platform: 'android',
        device_public_key: expect.any(String),
      }),
    )
    expect(mocks.storage.has(DEVICE_PRIVATE_KEY_STORAGE_KEY)).toBe(true)
    expect(mocks.storage.get(DEVICE_ID_STORAGE_KEY)).toBe('device-1')
    expect(mocks.storage.has(DEVICE_ENROLLMENT_TOKEN_STORAGE_KEY)).toBe(false)
  })

  it('fails before key generation and network when enrollment authorization is absent', async () => {
    await expect(generateAndEnrollDevice()).rejects.toThrow(
      'Device enrollment authorization is missing or expired',
    )
    expect(mocks.post).not.toHaveBeenCalled()
    expect(mocks.storage.has(DEVICE_PRIVATE_KEY_STORAGE_KEY)).toBe(false)
  })

  it('fails before network when the patient JWT is absent', async () => {
    mocks.storage.set(DEVICE_ENROLLMENT_TOKEN_STORAGE_KEY, 'enrollment-token-value')
    mocks.accessToken = null

    await expect(generateAndEnrollDevice()).rejects.toThrow('Patient session is missing or expired')
    expect(mocks.post).not.toHaveBeenCalled()
  })

  it('reports a missing native SecureStore module before network access', async () => {
    mocks.secureStoreAvailable = false

    await expect(generateAndEnrollDevice()).rejects.toThrow(
      'Install a development build that includes expo-secure-store',
    )
    expect(mocks.post).not.toHaveBeenCalled()
  })
})
