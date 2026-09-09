import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  accessToken: 'patient-token' as string | null,
  enrollmentToken: 'fresh-enrollment-token' as string | null,
  localDeviceId: null as string | null,
  currentNative: null as any,
  pendingNative: null as any,
  legacy: null as any,
  serverDevices: [] as any[],
  enroll: vi.fn(),
  setDeviceId: vi.fn(),
  ensurePending: vi.fn(),
  commitPending: vi.fn(),
  migrateLegacy: vi.fn(),
  clearSession: vi.fn(),
}))

vi.mock('react-native', () => ({ Platform: { OS: 'android' } }))
vi.mock('expo-secure-store', () => ({
  WHEN_UNLOCKED_THIS_DEVICE_ONLY: 'WHEN_UNLOCKED_THIS_DEVICE_ONLY',
  getItemAsync: vi.fn(async (key: string) =>
    key === 'enrollment-token-key' ? mocks.enrollmentToken : null
  ),
  setItemAsync: vi.fn(),
  deleteItemAsync: vi.fn(async (key: string) => {
    if (key === 'enrollment-token-key') mocks.enrollmentToken = null
  }),
}))
vi.mock('../utils/apiClient', async (importOriginal) => {
  const original = await importOriginal<typeof import('../utils/apiClient')>()
  return { ...original, getAuthToken: vi.fn(async () => mocks.accessToken) }
})
vi.mock('./patientAuthSession', () => ({
  DEVICE_ENROLLMENT_TOKEN_STORAGE_KEY: 'enrollment-token-key',
  clearPatientAuthSession: mocks.clearSession,
}))
vi.mock('./deviceKeys', () => ({
  DEVICE_ID_STORAGE_KEY: 'device-id-key',
  getDeviceId: vi.fn(async () => mocks.localDeviceId),
  getDevices: vi.fn(async () => ({ patient_id: 'patient-1', devices: mocks.serverDevices })),
  enrollDevice: mocks.enroll,
  setDeviceId: mocks.setDeviceId.mockImplementation(async (deviceId: string) => {
    mocks.localDeviceId = deviceId
  }),
  getDeviceLabel: vi.fn(() => 'Android test device'),
}))
vi.mock('./legacyDeviceKey', () => ({
  getLegacyDeviceKeyInfo: vi.fn(async () => mocks.legacy),
  deleteLegacyDevicePrivateKey: vi.fn(),
}))
vi.mock('./nativeDeviceKeyring', () => ({
  getCurrentNativeDeviceKey: vi.fn(async () => mocks.currentNative),
  getPendingNativeDeviceKey: vi.fn(async () => mocks.pendingNative),
  ensurePendingNativeDeviceKey: mocks.ensurePending,
  commitPendingNativeDeviceKey: mocks.commitPending,
  fingerprintPublicKeyDerBase64: vi.fn(async (value: string) => `${value}-fingerprint`),
}))
vi.mock('./patientDeviceRotation', () => ({
  migrateLegacyDeviceToNative: mocks.migrateLegacy,
}))

async function service() {
  vi.resetModules()
  return import('./currentDeviceEnrollment')
}

function activeDevice(overrides: Record<string, unknown> = {}) {
  return {
    device_id: 'device-1',
    key_id: 'key-1',
    key_version: 1,
    device_label: 'Android',
    platform: 'android',
    status: 'active',
    enrolled_at: '2026-09-09T00:00:00Z',
    public_key_fingerprint: 'native-der-fingerprint',
    ...overrides,
  }
}

describe('native current installation reconciliation', () => {
  beforeEach(() => {
    mocks.accessToken = 'patient-token'
    mocks.enrollmentToken = 'fresh-enrollment-token'
    mocks.localDeviceId = null
    mocks.currentNative = null
    mocks.pendingNative = null
    mocks.legacy = null
    mocks.serverDevices = []
    mocks.enroll.mockReset()
    mocks.setDeviceId.mockClear()
    mocks.ensurePending.mockReset().mockResolvedValue({
      alias: 'pending-alias',
      publicKeyDerBase64: 'native-der',
      custody: 'android-keystore',
      platform: 'android',
      nonExportable: true,
      hardwareBacked: false,
      strongBoxBacked: false,
    })
    mocks.commitPending.mockReset().mockImplementation(async () => {
      mocks.currentNative = {
        alias: 'pending-alias',
        publicKeyDerBase64: 'native-der',
        custody: 'android-keystore',
        platform: 'android',
        nonExportable: true,
        hardwareBacked: false,
        strongBoxBacked: false,
      }
      mocks.pendingNative = null
      return mocks.currentNative
    })
    mocks.migrateLegacy.mockReset()
    mocks.clearSession.mockReset()
  })

  it('bootstraps with a pending native public key and never sends private material', async () => {
    mocks.enroll.mockResolvedValue({
      device_id: 'device-1', key_id: 'key-1', key_version: 1, status: 'active', patient_id: 'patient-1', enrolled_at: ''
    })
    mocks.serverDevices = []
    const { ensureCurrentDeviceEnrollment } = await service()
    const promise = ensureCurrentDeviceEnrollment()
    await vi.waitFor(() => expect(mocks.enroll).toHaveBeenCalledOnce())
    mocks.serverDevices = [activeDevice()]
    const result = await promise
    expect(result).toMatchObject({ deviceId: 'device-1', keyAlias: 'pending-alias', enrolledNow: true })
    const payload = mocks.enroll.mock.calls[0]?.[0]
    expect(payload.device_public_key).toBe('native-der')
    expect(JSON.stringify(payload)).not.toContain('private')
  })

  it('accepts only an active server row matching the current native fingerprint', async () => {
    mocks.localDeviceId = 'device-1'
    mocks.currentNative = {
      alias: 'native-alias', publicKeyDerBase64: 'native-der', custody: 'android-keystore', platform: 'android', nonExportable: true
    }
    mocks.serverDevices = [activeDevice()]
    const { ensureCurrentDeviceEnrollment } = await service()
    await expect(ensureCurrentDeviceEnrollment()).resolves.toMatchObject({
      deviceId: 'device-1', keyAlias: 'native-alias', enrolledNow: false
    })
    expect(mocks.enroll).not.toHaveBeenCalled()
  })

  it('reconciles a pending key after an ambiguous prior server commit', async () => {
    mocks.pendingNative = {
      alias: 'pending-alias', publicKeyDerBase64: 'native-der', custody: 'android-keystore', platform: 'android', nonExportable: true
    }
    mocks.serverDevices = [activeDevice()]
    const { ensureCurrentDeviceEnrollment } = await service()
    await expect(ensureCurrentDeviceEnrollment()).resolves.toMatchObject({ deviceId: 'device-1' })
    expect(mocks.commitPending).toHaveBeenCalledWith('pending-alias', { deletePrevious: true })
    expect(mocks.enroll).not.toHaveBeenCalled()
  })

  it('migrates an active legacy SecureStore key through proof-of-possession rotation', async () => {
    mocks.legacy = { publicKeyDerBase64: 'legacy-der', publicKeyFingerprint: 'legacy-fingerprint' }
    mocks.serverDevices = [activeDevice({ public_key_fingerprint: 'legacy-fingerprint' })]
    mocks.migrateLegacy.mockResolvedValue({
      rotation: { device_id: 'device-1', new_key_id: 'key-2', new_key_version: 2 },
    })
    mocks.currentNative = {
      alias: 'native-alias', publicKeyDerBase64: 'native-der', custody: 'android-keystore', platform: 'android', nonExportable: true
    }
    mocks.serverDevices.push(activeDevice({ key_id: 'key-2', key_version: 2 }))
    const { ensureCurrentDeviceEnrollment } = await service()
    await expect(ensureCurrentDeviceEnrollment()).resolves.toMatchObject({ keyVersion: 2 })
    expect(mocks.migrateLegacy).toHaveBeenCalledOnce()
    expect(mocks.enroll).not.toHaveBeenCalled()
  })

  it('never reuses bootstrap enrollment after any device history exists', async () => {
    mocks.serverDevices = [activeDevice({ status: 'revoked' })]
    const { ensureCurrentDeviceEnrollment } = await service()
    await expect(ensureCurrentDeviceEnrollment()).rejects.toMatchObject({
      code: 'RECOVERY_REQUIRED', status: 409
    })
    expect(mocks.enroll).not.toHaveBeenCalled()
  })

  it('clears the patient session when the backend rejects enrollment authority with 401', async () => {
    const { ApiError } = await import('../utils/apiClient')
    mocks.enroll.mockRejectedValue(new ApiError('expired', 401, 'REAUTH_REQUIRED'))
    const { ensureCurrentDeviceEnrollment } = await service()
    await expect(ensureCurrentDeviceEnrollment()).rejects.toMatchObject({ code: 'REAUTH_REQUIRED' })
    expect(mocks.clearSession).toHaveBeenCalledWith('expired')
  })

  it('does not generate authority when enrollment is disallowed', async () => {
    const { ensureCurrentDeviceEnrollment } = await service()
    await expect(ensureCurrentDeviceEnrollment({ allowEnrollment: false })).rejects.toMatchObject({
      code: 'SETUP_REQUIRED'
    })
    expect(mocks.ensurePending).not.toHaveBeenCalled()
  })
})
