import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  localDeviceId: 'device-1' as string | null,
  devices: [] as any[],
  revoke: vi.fn(),
  clearNative: vi.fn(),
  deleteItem: vi.fn(),
  rotate: vi.fn(),
}))

vi.mock('expo-secure-store', () => ({ deleteItemAsync: mocks.deleteItem }))
vi.mock('../utils/apiClient', () => ({
  NexaApiClient: { revokeDevice: mocks.revoke },
}))
vi.mock('./deviceKeys', () => ({
  DEVICE_ID_STORAGE_KEY: 'device-id-key',
  getDeviceId: vi.fn(async () => mocks.localDeviceId),
  getDevices: vi.fn(async () => ({ patient_id: 'patient-1', devices: mocks.devices })),
}))
vi.mock('./nativeDeviceKeyring', () => ({ clearCurrentNativeDeviceKey: mocks.clearNative }))
vi.mock('./patientDeviceRotation', () => ({ rotateCurrentNativeDeviceKey: mocks.rotate }))

const device = (overrides: Record<string, unknown> = {}) => ({
  device_id: 'device-1',
  key_id: 'key-1',
  key_version: 1,
  device_label: 'Pixel',
  platform: 'android',
  status: 'active',
  enrolled_at: '2026-09-09T00:00:00Z',
  public_key_fingerprint: 'fingerprint-1',
  ...overrides,
})

describe('trusted patient device management', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.localDeviceId = 'device-1'
    mocks.devices = [device(), device({ device_id: 'device-2', key_id: 'key-2' })]
    mocks.revoke.mockResolvedValue({
      device_id: 'device-1', status: 'revoked', revoked_at: '2026-09-09T01:00:00Z'
    })
    mocks.clearNative.mockResolvedValue(undefined)
    mocks.deleteItem.mockResolvedValue(undefined)
  })

  it('marks only the exact local logical device as current', async () => {
    const { listManagedPatientDevices } = await import('./patientDeviceManagement')
    const devices = await listManagedPatientDevices()
    expect(devices.map((item) => [item.device_id, item.isCurrentInstallation])).toEqual([
      ['device-1', true],
      ['device-2', false],
    ])
  })

  it('revokes the server before deleting the current native key', async () => {
    const order: string[] = []
    mocks.revoke.mockImplementation(async () => {
      order.push('server')
      return { device_id: 'device-1', status: 'revoked', revoked_at: '' }
    })
    mocks.clearNative.mockImplementation(async () => {
      order.push('local-key')
    })
    mocks.deleteItem.mockImplementation(async () => {
      order.push('local-id')
    })
    const { revokeManagedPatientDevice } = await import('./patientDeviceManagement')
    await revokeManagedPatientDevice('device-1')
    expect(order).toEqual(['server', 'local-key', 'local-id'])
  })

  it('does not clear local authority when server revocation fails', async () => {
    mocks.revoke.mockRejectedValue(new Error('network down'))
    const { revokeManagedPatientDevice } = await import('./patientDeviceManagement')
    await expect(revokeManagedPatientDevice('device-1')).rejects.toThrow('network down')
    expect(mocks.clearNative).not.toHaveBeenCalled()
    expect(mocks.deleteItem).not.toHaveBeenCalled()
  })

  it('does not clear this installation when revoking another trusted device', async () => {
    mocks.revoke.mockResolvedValue({ device_id: 'device-2', status: 'revoked', revoked_at: '' })
    const { revokeManagedPatientDevice } = await import('./patientDeviceManagement')
    await revokeManagedPatientDevice('device-2')
    expect(mocks.clearNative).not.toHaveBeenCalled()
    expect(mocks.deleteItem).not.toHaveBeenCalled()
  })

  it('reconciles routine native rotation to the returned next key version', async () => {
    mocks.rotate.mockImplementation(async () => {
      mocks.devices = [device({ key_id: 'key-2', key_version: 2 })]
      return {
        rotation: { device_id: 'device-1', new_key_id: 'key-2', new_key_version: 2 },
      }
    })
    const { rotateCurrentManagedDevice } = await import('./patientDeviceManagement')
    await expect(rotateCurrentManagedDevice()).resolves.toMatchObject({
      device_id: 'device-1', key_id: 'key-2', key_version: 2, isCurrentInstallation: true,
    })
  })
})
