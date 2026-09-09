import { apiClient } from '../utils/apiClient'
import type { DeviceInfo, EnrollDeviceResponse } from './deviceKeys'
import {
  fingerprintPublicKeyDerBase64,
  getCurrentNativeDeviceKey,
} from './nativeDeviceKeyring'
import { signWithNativeDeviceKey } from './nativeDeviceSecurity'

interface TrustedEnrollmentChallengeResponse {
  challenge_nonce: string
  protocol_version: 'nexa-trusted-device-enrollment-v1'
  operation: 'authorize_trusted_device_enrollment'
  authorizer_device_id: string
  authorizer_key_version: number
  new_public_key_fingerprint: string
  issued_at: string
  expires_at: string
  signing_payload_b64: string
}

function decodeBase64Ascii(value: string): string {
  if (typeof atob === 'function') return atob(value)
  return Buffer.from(value, 'base64').toString('utf8')
}

function validateTrustedEnrollmentChallenge(
  challenge: TrustedEnrollmentChallengeResponse,
  authorizer: DeviceInfo,
  expectedFingerprint: string
): string {
  if (
    challenge.protocol_version !== 'nexa-trusted-device-enrollment-v1' ||
    challenge.operation !== 'authorize_trusted_device_enrollment' ||
    challenge.authorizer_device_id !== authorizer.device_id ||
    challenge.authorizer_key_version !== authorizer.key_version ||
    challenge.new_public_key_fingerprint !== expectedFingerprint
  ) {
    throw new Error('TRUSTED_ENROLLMENT_CHALLENGE_BINDING_MISMATCH')
  }
  const signingPayload = decodeBase64Ascii(challenge.signing_payload_b64)
  const parsed = JSON.parse(signingPayload) as Record<string, unknown>
  if (
    parsed.protocol_version !== challenge.protocol_version ||
    parsed.operation !== challenge.operation ||
    parsed.authorizer_device_id !== challenge.authorizer_device_id ||
    parsed.authorizer_key_version !== challenge.authorizer_key_version ||
    parsed.new_public_key_fingerprint !== expectedFingerprint ||
    parsed.challenge_nonce !== challenge.challenge_nonce ||
    parsed.issued_at !== challenge.issued_at ||
    parsed.expires_at !== challenge.expires_at
  ) {
    throw new Error('TRUSTED_ENROLLMENT_PAYLOAD_BINDING_MISMATCH')
  }
  return signingPayload
}

/**
 * Authorize a prospective device public key from this installation's current native key.
 * The prospective private key is never transferred to the authorizer or server.
 */
export async function authorizeProspectiveTrustedDevice(params: {
  authorizer: DeviceInfo
  newPublicKeyDerBase64: string
  deviceLabel: string
  platform: 'ios' | 'android'
}): Promise<EnrollDeviceResponse> {
  const current = await getCurrentNativeDeviceKey()
  if (!current) throw new Error('CURRENT_NATIVE_DEVICE_KEY_MISSING')
  const currentFingerprint = await fingerprintPublicKeyDerBase64(current.publicKeyDerBase64)
  if (
    params.authorizer.status !== 'active' ||
    currentFingerprint !== params.authorizer.public_key_fingerprint
  ) {
    throw new Error('TRUSTED_ENROLLMENT_AUTHORIZER_BINDING_MISMATCH')
  }

  const newFingerprint = await fingerprintPublicKeyDerBase64(params.newPublicKeyDerBase64)
  const { data: challenge } = await apiClient.post<TrustedEnrollmentChallengeResponse>(
    `/api/v2/patient/devices/${encodeURIComponent(params.authorizer.device_id)}/trusted-enrollment/challenge`,
    { new_device_public_key: params.newPublicKeyDerBase64 }
  )
  const signingPayload = validateTrustedEnrollmentChallenge(
    challenge,
    params.authorizer,
    newFingerprint
  )
  const signature = await signWithNativeDeviceKey(current.alias, signingPayload)
  const { data: enrollment } = await apiClient.post<EnrollDeviceResponse>(
    `/api/v2/patient/devices/${encodeURIComponent(params.authorizer.device_id)}/trusted-enrollment/authorize`,
    {
      challenge_nonce: challenge.challenge_nonce,
      authorizer_key_version: params.authorizer.key_version,
      new_device_public_key: params.newPublicKeyDerBase64,
      signature,
      device_label: params.deviceLabel,
      platform: params.platform,
    }
  )
  if (
    enrollment.status !== 'active' ||
    enrollment.key_version !== 1 ||
    enrollment.device_id === params.authorizer.device_id
  ) {
    throw new Error('TRUSTED_ENROLLMENT_RESPONSE_BINDING_MISMATCH')
  }
  return enrollment
}
