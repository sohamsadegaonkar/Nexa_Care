import { apiClient } from '../utils/apiClient'
import { deleteLegacyDevicePrivateKey, getLegacyDeviceKeyInfo, signLegacyDeviceMessage } from './legacyDeviceKey'
import {
  commitPendingNativeDeviceKey,
  ensurePendingNativeDeviceKey,
  fingerprintPublicKeyDerBase64,
  getCurrentNativeDeviceKey,
} from './nativeDeviceKeyring'
import { signWithNativeDeviceKey, type NativeDeviceKeyInfo } from './nativeDeviceSecurity'
import type { DeviceInfo } from './deviceKeys'

export interface DeviceRotationChallengeResponse {
  challenge_nonce: string
  protocol_version: 'nexa-device-key-rotation-v1'
  operation: 'rotate_device_key'
  device_id: string
  current_key_version: number
  new_public_key_fingerprint: string
  issued_at: string
  expires_at: string
  signing_payload_b64: string
}

export interface DeviceRotationResponse {
  device_id: string
  old_key_id: string
  new_key_id: string
  old_key_version: number
  new_key_version: number
  new_public_key_fingerprint: string
  status: 'active'
  rotated_at: string
}

function decodeBase64Ascii(value: string): string {
  if (typeof atob === 'function') return atob(value)
  return Buffer.from(value, 'base64').toString('utf8')
}

function validateChallenge(
  challenge: DeviceRotationChallengeResponse,
  device: DeviceInfo,
  expectedFingerprint: string
): string {
  if (
    challenge.protocol_version !== 'nexa-device-key-rotation-v1' ||
    challenge.operation !== 'rotate_device_key' ||
    challenge.device_id !== device.device_id ||
    challenge.current_key_version !== device.key_version ||
    challenge.new_public_key_fingerprint !== expectedFingerprint
  ) {
    throw new Error('DEVICE_ROTATION_CHALLENGE_BINDING_MISMATCH')
  }
  const signingPayload = decodeBase64Ascii(challenge.signing_payload_b64)
  const parsed = JSON.parse(signingPayload) as Record<string, unknown>
  if (
    parsed.protocol_version !== challenge.protocol_version ||
    parsed.operation !== challenge.operation ||
    parsed.device_id !== challenge.device_id ||
    parsed.current_key_version !== challenge.current_key_version ||
    parsed.new_public_key_fingerprint !== expectedFingerprint ||
    parsed.challenge_nonce !== challenge.challenge_nonce ||
    parsed.issued_at !== challenge.issued_at ||
    parsed.expires_at !== challenge.expires_at
  ) {
    throw new Error('DEVICE_ROTATION_PAYLOAD_BINDING_MISMATCH')
  }
  return signingPayload
}

async function issueChallenge(device: DeviceInfo, replacement: NativeDeviceKeyInfo) {
  const fingerprint = await fingerprintPublicKeyDerBase64(replacement.publicKeyDerBase64)
  const { data } = await apiClient.post<DeviceRotationChallengeResponse>(
    `/api/v2/patient/devices/${encodeURIComponent(device.device_id)}/rotation/challenge`,
    { new_device_public_key: replacement.publicKeyDerBase64 }
  )
  return { challenge: data, fingerprint, signingPayload: validateChallenge(data, device, fingerprint) }
}

async function finishRotation(
  device: DeviceInfo,
  replacement: NativeDeviceKeyInfo,
  challenge: DeviceRotationChallengeResponse,
  fingerprint: string,
  signature: string
): Promise<DeviceRotationResponse> {
  const { data } = await apiClient.post<DeviceRotationResponse>(
    `/api/v2/patient/devices/${encodeURIComponent(device.device_id)}/rotate`,
    {
      challenge_nonce: challenge.challenge_nonce,
      current_key_version: device.key_version,
      new_device_public_key: replacement.publicKeyDerBase64,
      signature,
    }
  )
  if (
    data.device_id !== device.device_id ||
    data.old_key_version !== device.key_version ||
    data.new_key_version !== device.key_version + 1 ||
    data.new_public_key_fingerprint !== fingerprint ||
    data.status !== 'active'
  ) {
    throw new Error('DEVICE_ROTATION_RESPONSE_BINDING_MISMATCH')
  }
  return data
}

export async function migrateLegacyDeviceToNative(
  device: DeviceInfo
): Promise<{ key: NativeDeviceKeyInfo; rotation: DeviceRotationResponse }> {
  const legacy = await getLegacyDeviceKeyInfo()
  if (!legacy || legacy.publicKeyFingerprint !== device.public_key_fingerprint) {
    throw new Error('LEGACY_DEVICE_KEY_BINDING_MISMATCH')
  }
  const replacement = await ensurePendingNativeDeviceKey()
  const { challenge, fingerprint, signingPayload } = await issueChallenge(device, replacement)
  const signature = await signLegacyDeviceMessage(signingPayload)
  const rotation = await finishRotation(device, replacement, challenge, fingerprint, signature)
  const key = await commitPendingNativeDeviceKey(replacement.alias, { deletePrevious: true })
  await deleteLegacyDevicePrivateKey()
  return { key, rotation }
}

export async function rotateCurrentNativeDeviceKey(
  device: DeviceInfo
): Promise<{ key: NativeDeviceKeyInfo; rotation: DeviceRotationResponse }> {
  const current = await getCurrentNativeDeviceKey()
  if (!current) throw new Error('CURRENT_NATIVE_DEVICE_KEY_MISSING')
  const currentFingerprint = await fingerprintPublicKeyDerBase64(current.publicKeyDerBase64)
  if (currentFingerprint !== device.public_key_fingerprint) {
    throw new Error('CURRENT_NATIVE_DEVICE_KEY_BINDING_MISMATCH')
  }

  const replacement = await ensurePendingNativeDeviceKey()
  if (replacement.alias === current.alias) throw new Error('DEVICE_ROTATION_ALIAS_REUSE')
  const { challenge, fingerprint, signingPayload } = await issueChallenge(device, replacement)
  const signature = await signWithNativeDeviceKey(current.alias, signingPayload)
  const rotation = await finishRotation(device, replacement, challenge, fingerprint, signature)
  const key = await commitPendingNativeDeviceKey(replacement.alias, { deletePrevious: true })
  return { key, rotation }
}
