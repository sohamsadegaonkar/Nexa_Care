import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  pending: {
    alias: 'pending-native',
    publicKeyDerBase64: 'native-der',
    custody: 'android-keystore',
    platform: 'android',
    nonExportable: true,
  },
  complete: vi.fn(),
  storeSession: vi.fn(),
  setDeviceId: vi.fn(),
  commit: vi.fn(),
  deleteLegacy: vi.fn(),
}))

vi.mock('react-native', () => ({ Platform: { OS: 'android' } }))
vi.mock('./nativeDeviceKeyring', () => ({
  ensurePendingNativeDeviceKey: vi.fn(async () => mocks.pending),
  fingerprintPublicKeyDerBase64: vi.fn(async () => 'fingerprint-1'),
  commitPendingNativeDeviceKey: mocks.commit,
}))
vi.mock('./patientRecovery', () => ({
  completePatientDeviceRecovery: mocks.complete,
}))
vi.mock('./patientAuthSession', () => ({
  storePatientAuthSession: mocks.storeSession,
}))
vi.mock('./deviceKeys', () => ({ setDeviceId: mocks.setDeviceId }))
vi.mock('./legacyDeviceKey', () => ({ deleteLegacyDevicePrivateKey: mocks.deleteLegacy }))

describe('native patient recovery', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.complete.mockResolvedValue({
      access_token: 'new-session',
      token_type: 'bearer',
      expires_at: '2026-09-09T08:00:00Z',
      patient_id: 'patient-1',
      device_id: 'device-1',
      key_id: 'key-1',
      key_version: 1,
      status: 'active',
      public_key_fingerprint: 'fingerprint-1',
      revoked_device_count: 2,
    })
    mocks.storeSession.mockResolvedValue(undefined)
    mocks.setDeviceId.mockResolvedValue(undefined)
    mocks.commit.mockResolvedValue(mocks.pending)
    mocks.deleteLegacy.mockResolvedValue(undefined)
  })

  it('sends only the pending native public key and promotes it after bound server recovery', async () => {
    const { completeNativePatientRecovery } = await import('./patientNativeRecovery')
    await expect(
      completeNativePatientRecovery({ recoveryToken: 'recovery-capability', deviceLabel: 'Pixel' })
    ).resolves.toMatchObject({ device_id: 'device-1', key_version: 1 })

    expect(mocks.complete).toHaveBeenCalledWith({
      recovery_token: 'recovery-capability',
      new_device_public_key: 'native-der',
      device_label: 'Pixel',
      platform: 'android',
    })
    expect(mocks.storeSession).toHaveBeenCalledWith('new-session', null)
    expect(mocks.setDeviceId).toHaveBeenCalledWith('device-1')
    expect(mocks.commit).toHaveBeenCalledWith('pending-native', { deletePrevious: true })
    expect(mocks.deleteLegacy).toHaveBeenCalledOnce()
    expect(JSON.stringify(mocks.complete.mock.calls[0]?.[0])).not.toContain('private')
  })

  it('does not promote a pending key when the server fingerprint is not the generated key', async () => {
    mocks.complete.mockResolvedValue({
      access_token: 'new-session',
      token_type: 'bearer',
      expires_at: '2026-09-09T08:00:00Z',
      patient_id: 'patient-1',
      device_id: 'device-1',
      key_id: 'key-1',
      key_version: 1,
      status: 'active',
      public_key_fingerprint: 'attacker-key',
      revoked_device_count: 2,
    })
    const { completeNativePatientRecovery } = await import('./patientNativeRecovery')
    await expect(
      completeNativePatientRecovery({ recoveryToken: 'recovery-capability', deviceLabel: 'Pixel' })
    ).rejects.toThrow('DEVICE_RECOVERY_RESPONSE_BINDING_MISMATCH')
    expect(mocks.storeSession).not.toHaveBeenCalled()
    expect(mocks.commit).not.toHaveBeenCalled()
  })
})
