import * as Crypto from 'expo-crypto'
import * as SecureStore from 'expo-secure-store'
import {
  deleteNativeDeviceKey,
  generateNativeDeviceKey,
  getNativeDeviceKey,
  type NativeDeviceKeyInfo,
} from './nativeDeviceSecurity'

export const DEVICE_NATIVE_KEY_ALIAS_STORAGE_KEY = 'nexa_device_native_key_alias_v1'
export const DEVICE_PENDING_NATIVE_KEY_ALIAS_STORAGE_KEY = 'nexa_device_pending_native_key_alias_v1'

function base64ToBytes(value: string): Uint8Array {
  if (typeof atob === 'function') {
    const binary = atob(value)
    const bytes = new Uint8Array(binary.length)
    for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index)
    return bytes
  }
  return new Uint8Array(Buffer.from(value, 'base64'))
}

export async function fingerprintPublicKeyDerBase64(value: string): Promise<string> {
  const digest = new Uint8Array(
    await Crypto.digest(Crypto.CryptoDigestAlgorithm.SHA256, base64ToBytes(value))
  )
  return Array.from(digest, (byte) => byte.toString(16).padStart(2, '0')).join('')
}

async function randomAlias(): Promise<string> {
  const bytes = await Crypto.getRandomBytesAsync(20)
  const suffix = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
  return `nexa-patient-signing-${suffix}`
}

async function readUsableAlias(storageKey: string): Promise<NativeDeviceKeyInfo | null> {
  const alias = await SecureStore.getItemAsync(storageKey)
  if (!alias) return null
  const key = await getNativeDeviceKey(alias)
  if (key) return key
  await SecureStore.deleteItemAsync(storageKey).catch(() => undefined)
  return null
}

export function getCurrentNativeDeviceKey(): Promise<NativeDeviceKeyInfo | null> {
  return readUsableAlias(DEVICE_NATIVE_KEY_ALIAS_STORAGE_KEY)
}

export function getPendingNativeDeviceKey(): Promise<NativeDeviceKeyInfo | null> {
  return readUsableAlias(DEVICE_PENDING_NATIVE_KEY_ALIAS_STORAGE_KEY)
}

export async function ensurePendingNativeDeviceKey(): Promise<NativeDeviceKeyInfo> {
  const existing = await getPendingNativeDeviceKey()
  if (existing) return existing

  const alias = await randomAlias()
  const key = await generateNativeDeviceKey(alias)
  try {
    await SecureStore.setItemAsync(DEVICE_PENDING_NATIVE_KEY_ALIAS_STORAGE_KEY, alias, {
      keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
    })
  } catch (error) {
    await deleteNativeDeviceKey(alias).catch(() => false)
    throw error
  }
  return key
}

export async function commitPendingNativeDeviceKey(
  alias: string,
  options: { deletePrevious?: boolean } = {}
): Promise<NativeDeviceKeyInfo> {
  const pendingAlias = await SecureStore.getItemAsync(DEVICE_PENDING_NATIVE_KEY_ALIAS_STORAGE_KEY)
  if (pendingAlias !== alias) throw new Error('PENDING_DEVICE_KEY_ALIAS_MISMATCH')
  const pending = await getNativeDeviceKey(alias)
  if (!pending) throw new Error('PENDING_DEVICE_KEY_MISSING')

  const previousAlias = await SecureStore.getItemAsync(DEVICE_NATIVE_KEY_ALIAS_STORAGE_KEY)
  await SecureStore.setItemAsync(DEVICE_NATIVE_KEY_ALIAS_STORAGE_KEY, alias, {
    keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
  })
  await SecureStore.deleteItemAsync(DEVICE_PENDING_NATIVE_KEY_ALIAS_STORAGE_KEY)

  if (options.deletePrevious !== false && previousAlias && previousAlias !== alias) {
    await deleteNativeDeviceKey(previousAlias).catch(() => false)
  }
  return pending
}

export async function discardPendingNativeDeviceKey(): Promise<void> {
  const alias = await SecureStore.getItemAsync(DEVICE_PENDING_NATIVE_KEY_ALIAS_STORAGE_KEY)
  if (alias) await deleteNativeDeviceKey(alias).catch(() => false)
  await SecureStore.deleteItemAsync(DEVICE_PENDING_NATIVE_KEY_ALIAS_STORAGE_KEY).catch(() => undefined)
}

export async function clearCurrentNativeDeviceKey(): Promise<void> {
  const alias = await SecureStore.getItemAsync(DEVICE_NATIVE_KEY_ALIAS_STORAGE_KEY)
  if (alias) await deleteNativeDeviceKey(alias).catch(() => false)
  await SecureStore.deleteItemAsync(DEVICE_NATIVE_KEY_ALIAS_STORAGE_KEY).catch(() => undefined)
}
