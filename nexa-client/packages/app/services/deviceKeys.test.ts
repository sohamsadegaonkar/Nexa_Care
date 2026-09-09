import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  storage: new Map<string, string>(),
  post: vi.fn(),
  get: vi.fn(),
  authenticate: vi.fn(),
}))

vi.mock('expo-secure-store', () => ({
  WHEN_UNLOCKED_THIS_DEVICE_ONLY: 'WHEN_UNLOCKED_THIS_DEVICE_ONLY',
  getItemAsync: vi.fn(async (key: string) => mocks.storage.get(key) ?? null),
  setItemAsync: vi.fn(async (key: string, value: string) => {
    mocks.storage.set(key, value)
  }),
}))
vi.mock('expo-local-authentication', () => ({
  hasHardwareAsync: vi.fn(async () => true),
  isEnrolledAsync: vi.fn(async () => true),
  authenticateAsync: mocks.authenticate,
}))
vi.mock('react-native', () => ({ Platform: { OS: 'android' } }))
vi.mock('../utils/apiClient', () => ({
  apiClient: { post: mocks.post, get: mocks.get },
}))

import {
  DEVICE_ID_STORAGE_KEY,
  authenticateWithBiometrics,
  constructConsentSigningInputV3,
  enrollDevice,
  getDeviceId,
  getDevices,
  setDeviceId,
} from './deviceKeys'

describe('canonical public device authority contract', () => {
  beforeEach(() => {
    mocks.storage.clear()
    mocks.post.mockReset()
    mocks.get.mockReset()
    mocks.authenticate.mockReset().mockResolvedValue({ success: true })
  })

  it('stores only the logical device identifier in this service', async () => {
    await setDeviceId('device-1')
    expect(mocks.storage.get(DEVICE_ID_STORAGE_KEY)).toBe('device-1')
    await expect(getDeviceId()).resolves.toBe('device-1')
  })

  it('enrolls only caller-supplied public key material', async () => {
    mocks.post.mockResolvedValue({
      data: {
        device_id: 'device-1',
        key_id: 'key-1',
        key_version: 1,
        status: 'active',
        patient_id: 'patient-1',
        enrolled_at: '2026-09-09T00:00:00Z',
      },
    })
    await enrollDevice({
      device_public_key: 'public-spki',
      device_label: 'Pixel',
      platform: 'android',
      device_enrollment_token: 'one-time-grant',
    })
    expect(mocks.post).toHaveBeenCalledWith(
      '/api/v2/patient/devices/enroll',
      expect.objectContaining({ device_public_key: 'public-spki' })
    )
    expect(JSON.stringify(mocks.post.mock.calls[0]?.[1])).not.toContain('private')
  })

  it('lists versioned device metadata without creating key authority', async () => {
    mocks.get.mockResolvedValue({ data: { patient_id: 'patient-1', devices: [] } })
    await expect(getDevices()).resolves.toEqual({ patient_id: 'patient-1', devices: [] })
    expect(mocks.get).toHaveBeenCalledWith('/api/v2/patient/devices')
  })

  it('keeps V3 canonical signing input bound to exact device key version and fingerprint', () => {
    const payload = constructConsentSigningInputV3({
      request_id: 'request-1',
      patient_id: 'patient-1',
      provider_id: 'provider-1',
      hospital_id: 'hospital-1',
      challenge_nonce: 'nonce-1',
      decision: 'approved',
      scope: 'summary',
      purpose: 'treatment',
      access_duration: 300,
      issued_at: '2026-09-09T00:00:00Z',
      expires_at: '2026-09-09T00:05:00Z',
      consent_context_hash: 'a'.repeat(64),
      device_id: 'device-1',
      key_id: 'key-2',
      key_version: 2,
      public_key_fingerprint: 'b'.repeat(64),
    })
    expect(JSON.parse(payload)).toMatchObject({
      protocol_version: 'nexa-consent-v3',
      domain: 'NEXA_CARE_SIGNED_CONSENT',
      operation: 'CONSENT_DECISION',
      device_id: 'device-1',
      key_id: 'key-2',
      key_version: 2,
      public_key_fingerprint: 'b'.repeat(64),
    })
  })

  it('uses local authentication only as a user-verification gate', async () => {
    await expect(authenticateWithBiometrics()).resolves.toBeUndefined()
    expect(mocks.authenticate).toHaveBeenCalledOnce()
  })
})
