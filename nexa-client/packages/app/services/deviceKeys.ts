/**
 * Device key generation and enrollment service (ALPHA).
 *
 * Generates a P-256 (secp256r1) keypair for biometric consent signing.
 * The private key is stored in expo-secure-store (backed by iOS Keychain /
 * Android Keystore at rest).  The public key is exported as DER/base64
 * and enrolled with the backend via POST /api/v2/patient/devices/enroll.
 *
 * Architecture note — deliberately chosen over react-native-biometrics:
 * react-native-biometrics generates RSA keys via native Keychain/Keystore
 * and produces RSA-PKCS1 signatures, which are incompatible with the
 * ECDSA-P-256 verification already built on the backend (see
 * app/services/biometric_signature_verifier.py).
 *
 * Honest ALPHA status:
 * Alpha: P-256 keypair generated client-side and private key stored in platform secure storage.
 *
 * Not yet: hardware-backed non-exportable signing key with biometric-gated key usage.
 *
 *   For an academic/incubator demo, this is strong.  For hospital pilot
 *   security, it still needs deeper native/hardware-backed key handling
 *   (Secure Enclave / StrongBox native module).
 *
 *   NOT YET VERIFIED ON DEVICE — this file was written in a sandboxed
 *   environment with no ability to run a native Expo build.  Test on a
 *   real device or simulator before relying on it for a demo.
 */

import * as SecureStore from 'expo-secure-store'
import * as Crypto from 'expo-crypto'
import * as LocalAuthentication from 'expo-local-authentication'
import { p256 } from '@noble/curves/p256'
import { Platform } from 'react-native'
import { apiClient, getAuthToken } from '../utils/apiClient'
import { DEVICE_ENROLLMENT_TOKEN_STORAGE_KEY } from './patientAuthSession'

export {
  DEVICE_ENROLLMENT_TOKEN_STORAGE_KEY,
  PATIENT_ACCESS_TOKEN_STORAGE_KEY,
  configurePatientAuthTokenProvider,
  storePatientAuthSession,
} from './patientAuthSession'

// ── Constants ────────────────────────────────────────────────────────────────

export const DEVICE_PRIVATE_KEY_STORAGE_KEY = 'nexa_device_private_key_v1'
export const DEVICE_ID_STORAGE_KEY = 'nexa_device_id_v1'

export type DeviceEnrollmentStage = 'generating' | 'enrolling'

// ── Types ────────────────────────────────────────────────────────────────────

export interface DeviceKeyResult {
  /** Base64-encoded DER (X.509 SubjectPublicKeyInfo) public key */
  publicKeyDerBase64: string
}

export interface EnrollDeviceParams {
  device_public_key: string
  device_label: string
  platform: string
  device_enrollment_token: string
  expo_push_token?: string
}

export interface EnrollDeviceResponse {
  device_id: string
  status: string
  patient_id: string
  enrolled_at: string
}

export interface DeviceInfo {
  device_id: string
  device_label: string | null
  platform: string
  status: string
  enrolled_at: string
  public_key_fingerprint: string
}

export interface DevicesListResponse {
  patient_id: string
  devices: DeviceInfo[]
}

// ── Internal helpers ─────────────────────────────────────────────────────────

function bytesToBase64(bytes: Uint8Array): string {
  let binary = ''
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i]!)
  }
  // eslint-disable-next-line no-undef
  return typeof btoa === 'function' ? btoa(binary) : Buffer.from(bytes).toString('base64')
}

function base64ToBytes(b64: string): Uint8Array {
  // eslint-disable-next-line no-undef
  if (typeof atob === 'function') {
    const binary = atob(b64)
    const bytes = new Uint8Array(binary.length)
    for (let i = 0; i < binary.length; i++) {
      bytes[i] = binary.charCodeAt(i)
    }
    return bytes
  }
  return new Uint8Array(Buffer.from(b64, 'base64'))
}

/** SHA-256 fingerprint of the raw DER bytes, matching the backend response. */
export async function fingerprintDevicePublicKey(publicKeyDerBase64: string): Promise<string> {
  const digest = new Uint8Array(
    await Crypto.digest(Crypto.CryptoDigestAlgorithm.SHA256, base64ToBytes(publicKeyDerBase64))
  )
  return Array.from(digest, (value) => value.toString(16).padStart(2, '0')).join('')
}

async function requireSecureStore(): Promise<void> {
  try {
    if (!(await SecureStore.isAvailableAsync())) {
      throw new Error('Secure storage is unavailable on this device.')
    }
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error)
    throw new Error(
      `Secure storage is unavailable (${detail}). Install a development build that includes expo-secure-store.`
    )
  }
}

async function generatePrivateKey(): Promise<Uint8Array> {
  for (let attempt = 0; attempt < 8; attempt += 1) {
    const candidate = await Crypto.getRandomBytesAsync(32)
    if (p256.utils.isValidPrivateKey(candidate)) return candidate
  }
  throw new Error(
    'Unable to generate a valid P-256 private key using the device secure random source.'
  )
}

/**
 * Wrap a raw EC public key (SEC1 uncompressed point, 65 bytes) in a
 * minimal X.509 SubjectPublicKeyInfo DER structure for the P-256 curve,
 * so the backend's `serialization.load_der_public_key` can parse it
 * directly with no format translation on the server side.
 */
function wrapEcPublicKeyAsDer(rawPoint: Uint8Array): Uint8Array {
  // SubjectPublicKeyInfo ::= SEQUENCE {
  //   algorithm AlgorithmIdentifier { id-ecPublicKey, prime256v1 },
  //   subjectPublicKey BIT STRING }
  const ecPublicKeyOid = [0x06, 0x07, 0x2a, 0x86, 0x48, 0xce, 0x3d, 0x02, 0x01]
  const prime256v1Oid = [0x06, 0x08, 0x2a, 0x86, 0x48, 0xce, 0x3d, 0x03, 0x01, 0x07]
  const algIdContent = [...ecPublicKeyOid, ...prime256v1Oid]
  const algId = [0x30, algIdContent.length, ...algIdContent]

  const bitString = [0x03, rawPoint.length + 1, 0x00, ...Array.from(rawPoint)]

  const spkiContent = [...algId, ...bitString]
  const spki = [0x30, spkiContent.length, ...spkiContent]

  return new Uint8Array(spki)
}

/**
 * Build a human-readable device label from the platform and current date.
 */
export function getDeviceLabel(): string {
  const deviceName = Platform.OS === 'ios' ? 'iPhone' : 'Android'
  const dateStr = new Date().toLocaleDateString()
  return `${deviceName} — ${dateStr}`
}

// ── Public API ───────────────────────────────────────────────────────────────

/**
 * Generate a P-256 keypair and store the private key in SecureStore.
 *
 * If a key already exists in SecureStore, returns the existing public key
 * without regenerating.  The private key is stored with
 * `keychainAccessible: WHEN_UNLOCKED_THIS_DEVICE_ONLY` so it never
 * leaves the device — it is NEVER sent to the backend.
 *
 * ALPHA: P-256 keypair generated client-side and private key stored in platform secure storage.
 * Not yet: hardware-backed non-exportable signing key with biometric-gated key usage.
 */
export async function generateDeviceKeypair(): Promise<DeviceKeyResult> {
  await requireSecureStore()

  // Check if a key already exists in secure storage
  const existing = await SecureStore.getItemAsync(DEVICE_PRIVATE_KEY_STORAGE_KEY)

  if (existing) {
    const privateKey = base64ToBytes(existing)
    if (!p256.utils.isValidPrivateKey(privateKey)) {
      throw new Error('The stored device key is invalid. Clear the app data and sign in again.')
    }
    const publicKey = p256.getPublicKey(privateKey, false) // uncompressed
    return { publicKeyDerBase64: bytesToBase64(wrapEcPublicKeyAsDer(publicKey)) }
  }

  // Expo Crypto supplies native secure randomness; this avoids relying on
  // crypto.getRandomValues, which is not guaranteed in React Native Hermes.
  const privateKey = await generatePrivateKey()
  await SecureStore.setItemAsync(DEVICE_PRIVATE_KEY_STORAGE_KEY, bytesToBase64(privateKey), {
    keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
  })

  const publicKey = p256.getPublicKey(privateKey, false)
  return { publicKeyDerBase64: bytesToBase64(wrapEcPublicKeyAsDer(publicKey)) }
}

/**
 * Enroll the device public key with the backend.
 *
 * Uses the shared apiClient — no raw fetch, no axios, no localhost.
 * The private key is NEVER included in this request.
 */
export async function enrollDevice(params: EnrollDeviceParams): Promise<EnrollDeviceResponse> {
  const { data } = await apiClient.post<EnrollDeviceResponse>(
    '/api/v2/patient/devices/enroll',
    params as unknown as Record<string, unknown>
  )
  return data
}

/**
 * Fetch the list of enrolled devices for the current patient.
 *
 * Returns device status (active / revoked) so the UI can display
 * the device's trusted status.  Uses shared apiClient.
 */
export async function getDevices(): Promise<DevicesListResponse> {
  const { data } = await apiClient.get<DevicesListResponse>('/api/v2/patient/devices')
  return data
}

/**
 * Full enrollment flow: generate P-256 keypair + enroll with backend.
 *
 * 1. Generate P-256 keypair (private key stored in SecureStore).
 * 2. Send the public key (DER/base64) + device label + platform to the
 *    backend enrollment endpoint.
 * 3. Return the enrollment response with device_id and status.
 *
 * The private key NEVER leaves the device.
 */
export async function generateAndEnrollDevice(
  deviceLabel?: string,
  onStage?: (stage: DeviceEnrollmentStage) => void
): Promise<EnrollDeviceResponse> {
  await requireSecureStore()
  const enrollmentToken = await SecureStore.getItemAsync(DEVICE_ENROLLMENT_TOKEN_STORAGE_KEY)
  if (!enrollmentToken) {
    throw new Error('Device enrollment authorization is missing or expired. Sign in again.')
  }
  if (!(await getAuthToken())) {
    throw new Error('Patient session is missing or expired. Sign in again.')
  }

  onStage?.('generating')
  const { publicKeyDerBase64 } = await generateDeviceKeypair()
  onStage?.('enrolling')
  const enrollment = await enrollDevice({
    device_public_key: publicKeyDerBase64,
    device_label: deviceLabel ?? getDeviceLabel(),
    platform: Platform.OS === 'ios' ? 'ios' : 'android',
    device_enrollment_token: enrollmentToken,
  })
  await setDeviceId(enrollment.device_id)
  try {
    await SecureStore.deleteItemAsync(DEVICE_ENROLLMENT_TOKEN_STORAGE_KEY)
  } catch {
    console.warn('DEVICE_ENROLLMENT_TOKEN_CLEANUP_ERROR')
  }
  return enrollment
}

/**
 * Check whether a device signing key already exists in SecureStore.
 *
 * ALPHA: In production, also verify the key matches a backend-enrolled
 * device (the key could be orphaned if the backend was reset).
 */
export async function hasDeviceKey(): Promise<boolean> {
  return (await getStoredDevicePublicKey()) !== null
}

/** Return public installation metadata only when the matching private key is usable. */
export async function getStoredDevicePublicKey(): Promise<DeviceKeyResult | null> {
  await requireSecureStore()
  const existing = await SecureStore.getItemAsync(DEVICE_PRIVATE_KEY_STORAGE_KEY)
  if (!existing) return null
  const privateKey = base64ToBytes(existing)
  if (!p256.utils.isValidPrivateKey(privateKey)) {
    await deleteDeviceKey()
    return null
  }
  const publicKey = p256.getPublicKey(privateKey, false)
  return { publicKeyDerBase64: bytesToBase64(wrapEcPublicKeyAsDer(publicKey)) }
}

/**
 * Delete the device signing key from SecureStore.
 *
 * Use this when the device is revoked or during testing cleanup.
 * Does NOT revoke the device on the backend — call the revoke API separately.
 */
export async function deleteDeviceKey(): Promise<void> {
  await SecureStore.deleteItemAsync(DEVICE_PRIVATE_KEY_STORAGE_KEY)
  await SecureStore.deleteItemAsync(DEVICE_ID_STORAGE_KEY)
}

/**
 * Store the enrolled device ID in SecureStore for later use
 * (e.g. including device_id in signed approval payloads).
 */
export async function setDeviceId(deviceId: string): Promise<void> {
  await SecureStore.setItemAsync(DEVICE_ID_STORAGE_KEY, deviceId)
}

/**
 * Retrieve the enrolled device ID.
 * Returns null if the device has not been enrolled.
 */
export async function getDeviceId(): Promise<string | null> {
  const id = await SecureStore.getItemAsync(DEVICE_ID_STORAGE_KEY)
  return id ?? null
}

export interface ConsentSigningFields {
  request_id: string
  patient_id: string
  provider_id: string
  challenge_nonce: string
  decision: 'approved' | 'denied'
  scope: string
  purpose: string
  access_duration: number
  issued_at: string
  expires_at: string
  device_id: string
}
export function constructConsentSigningInput(params: ConsentSigningFields): string {
  return JSON.stringify({
    access_duration: params.access_duration,
    challenge_nonce: params.challenge_nonce,
    decision: params.decision,
    device_id: params.device_id,
    expires_at: params.expires_at,
    issued_at: params.issued_at,
    patient_id: params.patient_id,
    protocol_version: 'nexa-consent-v2',
    provider_id: params.provider_id,
    purpose: params.purpose,
    request_id: params.request_id,
    scope: params.scope,
  })
}
export async function authenticateWithBiometrics(): Promise<void> {
  if (
    !(await LocalAuthentication.hasHardwareAsync()) ||
    !(await LocalAuthentication.isEnrolledAsync())
  )
    throw new Error('Biometric authentication is not available on this device.')
  const result = await LocalAuthentication.authenticateAsync({
    promptMessage: 'Confirm your identity to approve this request',
    fallbackLabel: 'Use Passcode',
    cancelLabel: 'Cancel',
  })
  if (!result.success) throw new Error('Biometric verification cancelled.')
}
/** ALPHA: SecureStore-backed JS signing, not hardware-backed non-exportable signing. */
export async function signConsentChallenge(params: ConsentSigningFields): Promise<string> {
  const encodedPrivateKey = await SecureStore.getItemAsync(DEVICE_PRIVATE_KEY_STORAGE_KEY)
  if (!encodedPrivateKey)
    throw new Error(
      'This device is not enrolled. Please secure this device before approving consent.'
    )
  const message = new TextEncoder().encode(constructConsentSigningInput(params))
  const digest = new Uint8Array(await Crypto.digest(Crypto.CryptoDigestAlgorithm.SHA256, message))
  return bytesToBase64(p256.sign(digest, base64ToBytes(encodedPrivateKey)).toDERRawBytes())
}
