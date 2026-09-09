import { requireOptionalNativeModule } from 'expo-modules-core'

export type NativeDeviceKeyCustody =
  | 'ios-secure-enclave'
  | 'android-strongbox'
  | 'android-keystore-hardware'
  | 'android-keystore'

export interface NativeDeviceKeyInfo {
  alias: string
  publicKeyDerBase64: string
  platform: 'ios' | 'android'
  custody: NativeDeviceKeyCustody
  nonExportable: boolean
  hardwareBacked: boolean
  strongBoxBacked: boolean
}

interface NexaDeviceSecurityNativeModule {
  generateKey(alias: string): Promise<NativeDeviceKeyInfo>
  getKey(alias: string): Promise<NativeDeviceKeyInfo | null>
  sign(alias: string, message: string): Promise<string>
  deleteKey(alias: string): Promise<boolean>
}

export class NativeDeviceSecurityUnavailableError extends Error {
  readonly code = 'NATIVE_DEVICE_SECURITY_UNAVAILABLE'

  constructor() {
    super('Native device signing is unavailable. Install a Nexa Care development or production build.')
    this.name = 'NativeDeviceSecurityUnavailableError'
  }
}

function nativeModule(): NexaDeviceSecurityNativeModule {
  const module = requireOptionalNativeModule<NexaDeviceSecurityNativeModule>('NexaDeviceSecurity')
  if (!module) throw new NativeDeviceSecurityUnavailableError()
  return module
}

export function isNativeDeviceSecurityAvailable(): boolean {
  return requireOptionalNativeModule<NexaDeviceSecurityNativeModule>('NexaDeviceSecurity') !== null
}

export async function generateNativeDeviceKey(alias: string): Promise<NativeDeviceKeyInfo> {
  const info = await nativeModule().generateKey(alias)
  assertNativeKeyInfo(info, alias)
  return info
}

export async function getNativeDeviceKey(alias: string): Promise<NativeDeviceKeyInfo | null> {
  const info = await nativeModule().getKey(alias)
  if (info === null) return null
  assertNativeKeyInfo(info, alias)
  return info
}

export async function signWithNativeDeviceKey(alias: string, message: string): Promise<string> {
  if (!message) throw new Error('DEVICE_SIGNING_INPUT_EMPTY')
  const signature = await nativeModule().sign(alias, message)
  if (!signature || typeof signature !== 'string') throw new Error('DEVICE_SIGNATURE_INVALID')
  return signature
}

export async function deleteNativeDeviceKey(alias: string): Promise<boolean> {
  return nativeModule().deleteKey(alias)
}

function assertNativeKeyInfo(info: NativeDeviceKeyInfo, alias: string): void {
  if (
    !info ||
    info.alias !== alias ||
    !info.publicKeyDerBase64 ||
    !['ios', 'android'].includes(info.platform) ||
    info.nonExportable !== true
  ) {
    throw new Error('NATIVE_DEVICE_KEY_ATTESTATION_INVALID')
  }
  if (info.custody === 'ios-secure-enclave' && info.hardwareBacked !== true) {
    throw new Error('NATIVE_DEVICE_KEY_ATTESTATION_INVALID')
  }
  if (info.custody === 'android-strongbox' && info.strongBoxBacked !== true) {
    throw new Error('NATIVE_DEVICE_KEY_ATTESTATION_INVALID')
  }
}
