import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  accessToken: 'patient-token' as string | null,
  enrollmentToken: 'fresh-enrollment-token' as string | null,
  localDeviceId: null as string | null,
  publicKey: null as string | null,
  serverDevices: [] as Array<{
    device_id: string
    status: string
    public_key_fingerprint?: string
  }>,
  enroll: vi.fn(),
  generate: vi.fn(),
  setDeviceId: vi.fn(),
  deleteDeviceKey: vi.fn(),
  clearSession: vi.fn(),
}))

vi.mock('react-native', () => ({ Platform: { OS: 'android' } }))
vi.mock('expo-secure-store', () => ({
  getItemAsync: vi.fn(async () => mocks.enrollmentToken),
  deleteItemAsync: vi.fn(async () => {
    mocks.enrollmentToken = null
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
  DEVICE_PRIVATE_KEY_STORAGE_KEY: 'private-key-alias',
  getDeviceId: vi.fn(async () => mocks.localDeviceId),
  getStoredDevicePublicKey: vi.fn(async () =>
    mocks.publicKey ? { publicKeyDerBase64: mocks.publicKey } : null
  ),
  fingerprintDevicePublicKey: vi.fn(async () => 'public-key-fingerprint'),
  getDevices: vi.fn(async () => ({ patient_id: 'patient-1', devices: mocks.serverDevices })),
  generateDeviceKeypair: mocks.generate.mockImplementation(async () => {
    mocks.publicKey = 'new-public-key'
    return { publicKeyDerBase64: mocks.publicKey }
  }),
  enrollDevice: mocks.enroll,
  setDeviceId: mocks.setDeviceId.mockImplementation(async (deviceId: string) => {
    mocks.localDeviceId = deviceId
  }),
  deleteDeviceKey: mocks.deleteDeviceKey.mockImplementation(async () => {
    mocks.localDeviceId = null
    mocks.publicKey = null
  }),
  getDeviceLabel: vi.fn(() => 'Android test device'),
}))

async function service() {
  vi.resetModules()
  return import('./currentDeviceEnrollment')
}

function successfulEnrollment(deviceId = 'new-device') {
  mocks.enroll.mockResolvedValue({
    device_id: deviceId,
    patient_id: 'patient-1',
    status: 'active',
    enrolled_at: '2026-07-15T00:00:00Z',
  })
}

describe('current installation enrollment reconciliation', () => {
  beforeEach(() => {
    mocks.accessToken = 'patient-token'
    mocks.enrollmentToken = 'fresh-enrollment-token'
    mocks.localDeviceId = null
    mocks.publicKey = null
    mocks.serverDevices = []
    mocks.enroll.mockReset()
    mocks.generate.mockClear()
    mocks.setDeviceId.mockClear()
    mocks.deleteDeviceKey.mockClear()
    mocks.clearSession.mockReset()
    successfulEnrollment()
  })

  it('enrolls this installation when the server has no devices', async () => {
    const { ensureCurrentDeviceEnrollment } = await service()
    const result = await ensureCurrentDeviceEnrollment()
    expect(result).toMatchObject({ deviceId: 'new-device', enrolledNow: true })
    expect(mocks.enroll).toHaveBeenCalledOnce()
    expect(mocks.setDeviceId).toHaveBeenCalledWith('new-device')
  })

  it('does not mistake another active patient device for this installation', async () => {
    mocks.serverDevices = [{ device_id: 'old-installation', status: 'active' }]
    const { ensureCurrentDeviceEnrollment } = await service()
    await ensureCurrentDeviceEnrollment()
    expect(mocks.enroll).toHaveBeenCalledOnce()
  })

  it('does not enroll when local device_id, local key, and active server row match', async () => {
    mocks.localDeviceId = 'current-device'
    mocks.publicKey = 'current-public-key'
    mocks.serverDevices = [
      {
        device_id: 'current-device',
        status: 'active',
        public_key_fingerprint: 'public-key-fingerprint',
      },
    ]
    const { ensureCurrentDeviceEnrollment } = await service()
    const result = await ensureCurrentDeviceEnrollment()
    expect(result).toMatchObject({ deviceId: 'current-device', enrolledNow: false })
    expect(mocks.enroll).not.toHaveBeenCalled()
    expect(mocks.enrollmentToken).toBeNull()
  })

  it.each([
    ['absent', []],
    ['revoked', [{ device_id: 'current-device', status: 'revoked' }]],
  ])('re-enrolls when the local device is %s on the server', async (_case, devices) => {
    mocks.localDeviceId = 'current-device'
    mocks.publicKey = 'current-public-key'
    mocks.serverDevices = devices
    const { ensureCurrentDeviceEnrollment } = await service()
    await ensureCurrentDeviceEnrollment()
    expect(mocks.enroll).toHaveBeenCalledOnce()
  })

  it('re-enrolls when device_id matches but the local key fingerprint does not', async () => {
    mocks.localDeviceId = 'current-device'
    mocks.publicKey = 'replacement-public-key'
    mocks.serverDevices = [
      {
        device_id: 'current-device',
        status: 'active',
        public_key_fingerprint: 'old-public-key-fingerprint',
      },
    ]
    const { ensureCurrentDeviceEnrollment } = await service()
    await ensureCurrentDeviceEnrollment()
    expect(mocks.enroll).toHaveBeenCalledOnce()
  })

  it('creates a fresh key and device identity when the local private key is missing', async () => {
    mocks.localDeviceId = 'stale-device'
    mocks.serverDevices = [
      {
        device_id: 'stale-device',
        status: 'active',
        public_key_fingerprint: 'different-fingerprint',
      },
    ]
    const { ensureCurrentDeviceEnrollment } = await service()
    await ensureCurrentDeviceEnrollment()
    expect(mocks.deleteDeviceKey).toHaveBeenCalledOnce()
    expect(mocks.generate).toHaveBeenCalledOnce()
    expect(mocks.enroll).toHaveBeenCalledOnce()
  })

  it('deduplicates concurrent enrollment attempts and React-style remount retries', async () => {
    let finish!: (value: unknown) => void
    mocks.enroll.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve
      })
    )
    const { ensureCurrentDeviceEnrollment } = await service()
    const first = ensureCurrentDeviceEnrollment()
    const second = ensureCurrentDeviceEnrollment()
    await vi.waitFor(() => expect(mocks.enroll).toHaveBeenCalledOnce())
    finish({ device_id: 'new-device', patient_id: 'patient-1', status: 'active', enrolled_at: '' })
    await Promise.all([first, second])
    mocks.serverDevices = [
      {
        device_id: 'new-device',
        status: 'active',
        public_key_fingerprint: 'public-key-fingerprint',
      },
    ]
    await ensureCurrentDeviceEnrollment()
    expect(mocks.enroll).toHaveBeenCalledOnce()
  })

  it('clears the patient session when enrollment returns 401', async () => {
    const { ensureCurrentDeviceEnrollment } = await service()
    const { ApiError } = await import('../utils/apiClient')
    mocks.enroll.mockRejectedValue(new ApiError('expired', 401, 'REAUTH_REQUIRED'))
    await expect(ensureCurrentDeviceEnrollment()).rejects.toMatchObject({
      code: 'REAUTH_REQUIRED',
      status: 401,
    })
    expect(mocks.clearSession).toHaveBeenCalledWith('expired')
  })

  it('maps a device-limit conflict to an actionable error', async () => {
    const { ensureCurrentDeviceEnrollment } = await service()
    const { ApiError } = await import('../utils/apiClient')
    mocks.enroll.mockRejectedValue(new ApiError('maximum devices', 409, 'CONFLICT'))
    await expect(ensureCurrentDeviceEnrollment()).rejects.toMatchObject({
      code: 'DEVICE_CONFLICT',
      status: 409,
    })
  })

  it('requires fresh OTP when no unused enrollment token remains', async () => {
    mocks.enrollmentToken = null
    const { ensureCurrentDeviceEnrollment } = await service()
    await expect(ensureCurrentDeviceEnrollment()).rejects.toMatchObject({
      code: 'REAUTH_REQUIRED',
      status: 401,
    })
    expect(mocks.enroll).not.toHaveBeenCalled()
  })

  it('reports setup before biometrics when enrollment is disallowed', async () => {
    const { ensureCurrentDeviceEnrollment } = await service()
    await expect(ensureCurrentDeviceEnrollment({ allowEnrollment: false })).rejects.toMatchObject({
      code: 'SETUP_REQUIRED',
    })
    expect(mocks.enroll).not.toHaveBeenCalled()
  })

  it('never includes private key or tokens in the enrollment payload', async () => {
    mocks.publicKey = 'public-key-only'
    const { ensureCurrentDeviceEnrollment } = await service()
    await ensureCurrentDeviceEnrollment({ expoPushToken: 'push-token' })
    const payload = mocks.enroll.mock.calls[0]?.[0]
    expect(payload).toMatchObject({
      device_public_key: 'public-key-only',
      device_enrollment_token: 'fresh-enrollment-token',
      expo_push_token: 'push-token',
    })
    expect(JSON.stringify(payload)).not.toContain('private')
  })
})
