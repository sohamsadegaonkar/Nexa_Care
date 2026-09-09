import * as Crypto from 'expo-crypto'
import * as SecureStore from 'expo-secure-store'
import { p256 } from '@noble/curves/p256'
import { DEVICE_PRIVATE_KEY_STORAGE_KEY } from './deviceKeys'
import { fingerprintPublicKeyDerBase64 } from './nativeDeviceKeyring'

function base64ToBytes(value: string): Uint8Array {
  if (typeof atob === 'function') {
    const binary = atob(value)
    const bytes = new Uint8Array(binary.length)
    for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index)
    return bytes
  }
  return new Uint8Array(Buffer.from(value, 'base64'))
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = ''
  for (const byte of bytes) binary += String.fromCharCode(byte)
  return typeof btoa === 'function' ? btoa(binary) : Buffer.from(bytes).toString('base64')
}

function wrapP256PointAsSpki(rawPoint: Uint8Array): Uint8Array {
  const prefix = [
    0x30, 0x59, 0x30, 0x13, 0x06, 0x07, 0x2a, 0x86, 0x48, 0xce, 0x3d, 0x02, 0x01,
    0x06, 0x08, 0x2a, 0x86, 0x48, 0xce, 0x3d, 0x03, 0x01, 0x07, 0x03, 0x42, 0x00,
  ]
  return new Uint8Array([...prefix, ...rawPoint])
}

async function readLegacyPrivateKey(): Promise<Uint8Array | null> {
  const encoded = await SecureStore.getItemAsync(DEVICE_PRIVATE_KEY_STORAGE_KEY)
  if (!encoded) return null
  const key = base64ToBytes(encoded)
  if (!p256.utils.isValidPrivateKey(key)) throw new Error('LEGACY_DEVICE_KEY_INVALID')
  return key
}

export async function getLegacyDeviceKeyInfo(): Promise<{
  publicKeyDerBase64: string
  publicKeyFingerprint: string
} | null> {
  const privateKey = await readLegacyPrivateKey()
  if (!privateKey) return null
  const publicPoint = p256.getPublicKey(privateKey, false)
  const publicKeyDerBase64 = bytesToBase64(wrapP256PointAsSpki(publicPoint))
  return {
    publicKeyDerBase64,
    publicKeyFingerprint: await fingerprintPublicKeyDerBase64(publicKeyDerBase64),
  }
}

/** Legacy raw-key access is deliberately isolated to one-time 6H rotation migration. */
export async function signLegacyDeviceMessage(message: string): Promise<string> {
  const privateKey = await readLegacyPrivateKey()
  if (!privateKey) throw new Error('LEGACY_DEVICE_KEY_MISSING')
  const digest = new Uint8Array(
    await Crypto.digest(Crypto.CryptoDigestAlgorithm.SHA256, new TextEncoder().encode(message))
  )
  return bytesToBase64(p256.sign(digest, privateKey).toDERRawBytes())
}

export async function deleteLegacyDevicePrivateKey(): Promise<void> {
  await SecureStore.deleteItemAsync(DEVICE_PRIVATE_KEY_STORAGE_KEY)
}
