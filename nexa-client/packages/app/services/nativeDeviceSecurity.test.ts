import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  native: null as null | {
    generateKey: ReturnType<typeof vi.fn>
    getKey: ReturnType<typeof vi.fn>
    sign: ReturnType<typeof vi.fn>
    deleteKey: ReturnType<typeof vi.fn>
  },
}))

vi.mock('expo-modules-core', () => ({
  requireOptionalNativeModule: vi.fn(() => mocks.native),
}))

import {
  NativeDeviceSecurityUnavailableError,
  deleteNativeDeviceKey,
  generateNativeDeviceKey,
  getNativeDeviceKey,
  isNativeDeviceSecurityAvailable,
  signWithNativeDeviceKey,
} from './nativeDeviceSecurity'

function moduleStub() {
  return {
    generateKey: vi.fn(),
    getKey: vi.fn(),
    sign: vi.fn(),
    deleteKey: vi.fn(),
  }
}

describe('native device security bridge', () => {
  beforeEach(() => {
    mocks.native = null
  })

  it('fails closed when the native module is not linked', async () => {
    expect(isNativeDeviceSecurityAvailable()).toBe(false)
    await expect(generateNativeDeviceKey('nexa-device-1')).rejects.toBeInstanceOf(
      NativeDeviceSecurityUnavailableError
    )
  })

  it('accepts a non-exportable Secure Enclave key description', async () => {
    const native = moduleStub()
    mocks.native = native
    native.generateKey.mockResolvedValue({
      alias: 'nexa-device-1',
      publicKeyDerBase64: 'DER',
      platform: 'ios',
      custody: 'ios-secure-enclave',
      nonExportable: true,
      hardwareBacked: true,
      strongBoxBacked: false,
    })

    await expect(generateNativeDeviceKey('nexa-device-1')).resolves.toMatchObject({
      custody: 'ios-secure-enclave',
      nonExportable: true,
    })
  })

  it('rejects a native response that overclaims Secure Enclave custody', async () => {
    const native = moduleStub()
    mocks.native = native
    native.generateKey.mockResolvedValue({
      alias: 'nexa-device-1',
      publicKeyDerBase64: 'DER',
      platform: 'ios',
      custody: 'ios-secure-enclave',
      nonExportable: true,
      hardwareBacked: false,
      strongBoxBacked: false,
    })

    await expect(generateNativeDeviceKey('nexa-device-1')).rejects.toThrow(
      'NATIVE_DEVICE_KEY_ATTESTATION_INVALID'
    )
  })

  it('signs by alias and never accepts private-key bytes', async () => {
    const native = moduleStub()
    mocks.native = native
    native.sign.mockResolvedValue('MEUCIQsignature')

    await expect(signWithNativeDeviceKey('nexa-device-1', '{"a":1}')).resolves.toBe(
      'MEUCIQsignature'
    )
    expect(native.sign).toHaveBeenCalledWith('nexa-device-1', '{"a":1}')
  })

  it('retrieves and deletes only by alias', async () => {
    const native = moduleStub()
    mocks.native = native
    native.getKey.mockResolvedValue({
      alias: 'nexa-device-1',
      publicKeyDerBase64: 'DER',
      platform: 'android',
      custody: 'android-keystore',
      nonExportable: true,
      hardwareBacked: false,
      strongBoxBacked: false,
    })
    native.deleteKey.mockResolvedValue(true)

    await expect(getNativeDeviceKey('nexa-device-1')).resolves.toMatchObject({ platform: 'android' })
    await expect(deleteNativeDeviceKey('nexa-device-1')).resolves.toBe(true)
  })
})
