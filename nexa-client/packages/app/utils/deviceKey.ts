/**
 * Device signing key management for Nexa Care patient app.
 *
 * Canonical signing contract (9-pipe, matches signed_approval_verifier.py):
 *   request_id|patient_id|provider_id|challenge_nonce|decision|scope|purpose|access_duration|expires_at
 *
 * This is the SAME format used by the consent approve-signed route
 * (SignedApprovalVerifier) and the consentSigning service. The older
 * 3-field concat format (nonce+requestId+patientId) used by
 * BiometricSignatureVerifier is superseded.
 *
 * Architecture note, deliberately chosen over react-native-biometrics:
 * react-native-biometrics generates RSA keys via native Keychain/Keystore
 * and produces RSA-PKCS1 signatures -- incompatible with the EC/ECDSA
 * verification already built and tested on the backend. Rewriting the
 * backend to accept RSA instead is a bigger, separate decision; matching
 * what's already there is the smaller, correct move.
 *
 * This implementation generates the P-256 keypair in JS with @noble/curves
 * (audited, widely used) and stores the private key via expo-secure-store,
 * which is backed by iOS Keychain / Android Keystore at rest. The
 * biometric gate (Face ID / Touch ID / fingerprint) happens via
 * expo-local-authentication immediately before each signing operation.
 *
 * Honest limitation vs. true hardware-backed signing: the private key is
 * briefly resident in JS memory during signing, unlike a key that never
 * leaves a Secure Enclave. It is still encrypted at rest by the OS
 * keystore, and biometric confirmation is still required to retrieve it.
 * If true never-leaves-hardware EC signing is required later, that means
 * a native module (e.g. a small Secure Enclave/StrongBox wrapper) --
 * scope that as its own task rather than assuming this covers it.
 *
 * ALPHA: P-256 keypair generated client-side and private key stored in
 * platform secure storage. Not yet: hardware-backed non-exportable
 * signing key with biometric-gated key usage.
 */

import * as SecureStore from 'expo-secure-store'
import { p256 } from '@noble/curves/p256'
import * as LocalAuthentication from 'expo-local-authentication'
import * as Crypto from 'expo-crypto'
import { apiClient } from '../utils/apiClient'

// ── Constants ────────────────────────────────────────────────────────────────

const PRIVATE_KEY_STORAGE_KEY = 'nexa_device_signing_private_key_v1'
const DEVICE_ID_STORAGE_KEY = 'nexa_device_id_v1'

// ── Internal helpers ─────────────────────────────────────────────────────────

function bytesToBase64(bytes: Uint8Array): string {
  let binary = ''
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i])
  // eslint-disable-next-line no-undef
  return typeof btoa === 'function' ? btoa(binary) : Buffer.from(bytes).toString('base64')
}

function base64ToBytes(b64: string): Uint8Array {
  // eslint-disable-next-line no-undef
  if (typeof atob === 'function') {
    const binary = atob(b64)
    const bytes = new Uint8Array(binary.length)
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
    return bytes
  }
  return new Uint8Array(Buffer.from(b64, 'base64'))
}

/**
 * Wraps a raw EC public key (SEC1 uncompressed point, 65 bytes) in a
 * minimal X.509 SubjectPublicKeyInfo DER structure for the P-256 curve,
 * so the backend's `serialization.load_der_public_key` can parse it
 * directly with no format translation on the server side.
 */
function wrapEcPublicKeyAsDer(rawPoint: Uint8Array): Uint8Array {
  const ecPublicKeyOid = [0x06, 0x07, 0x2a, 0x86, 0x48, 0xce, 0x3d, 0x02, 0x01]
  const prime256v1Oid = [0x06, 0x08, 0x2a, 0x86, 0x48, 0xce, 0x3d, 0x03, 0x01, 0x07]
  const algIdContent = [...ecPublicKeyOid, ...prime256v1Oid]
  const algId = [0x30, algIdContent.length, ...algIdContent]

  const bitString = [0x03, rawPoint.length + 1, 0x00, ...rawPoint]

  const spkiContent = [...algId, ...bitString]
  const spki = [0x30, spkiContent.length, ...spkiContent]

  return new Uint8Array(spki)
}

interface DeviceKeyPair {
  privateKey: Uint8Array
  publicKeyDer: Uint8Array
}

async function loadOrCreateKeyPair(): Promise<DeviceKeyPair> {
  const existing = await SecureStore.getItemAsync(PRIVATE_KEY_STORAGE_KEY, {
    requireAuthentication: true,
  })

  if (existing) {
    const privateKey = base64ToBytes(existing)
    const publicKey = p256.getPublicKey(privateKey, false) // uncompressed point
    return { privateKey, publicKeyDer: wrapEcPublicKeyAsDer(publicKey) }
  }

  const privateKey = p256.utils.randomPrivateKey()
  await SecureStore.setItemAsync(PRIVATE_KEY_STORAGE_KEY, bytesToBase64(privateKey), {
    requireAuthentication: true,
    keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
  })

  const publicKey = p256.getPublicKey(privateKey, false)
  return { privateKey, publicKeyDer: wrapEcPublicKeyAsDer(publicKey) }
}

// ── Public API: Key management ───────────────────────────────────────────────

/**
 * One-time (per device) enrollment: generate a P-256 keypair, keep the
 * private key in the OS keystore, and register the public key with the
 * backend via POST /api/v2/patient/devices/enroll.
 *
 * Call this once, e.g. on first login or from a "Security" settings
 * screen -- not on every approval.
 */
export async function enrollDeviceKey(): Promise<void> {
  const { publicKeyDer } = await loadOrCreateKeyPair()
  await apiClient.post('/api/v2/patient/devices/enroll', {
    device_public_key: bytesToBase64(publicKeyDer),
    device_label: 'Patient Device',
    platform: 'ios',
  } as unknown as Record<string, unknown>)
}

/**
 * Check whether a device signing key already exists in SecureStore.
 */
export async function hasDeviceKey(): Promise<boolean> {
  const existing = await SecureStore.getItemAsync(PRIVATE_KEY_STORAGE_KEY)
  return !!existing
}

/**
 * Store the enrolled device ID for later use in approval payloads.
 */
export async function setDeviceId(deviceId: string): Promise<void> {
  await SecureStore.setItemAsync(DEVICE_ID_STORAGE_KEY, deviceId)
}

/**
 * Retrieve the enrolled device ID.
 */
export async function getDeviceId(): Promise<string | null> {
  const id = await SecureStore.getItemAsync(DEVICE_ID_STORAGE_KEY)
  return id ?? null
}

// ── Public API: Signing ──────────────────────────────────────────────────────

/**
 * Canonical 9-attribute signing input that the backend verifier
 * (SignedApprovalVerifier) reconstructs and checks against.
 *
 * MUST match signed_approval_verifier.py byte-for-byte:
 *   f"{request_id}|{patient_id}|{provider_id or ''}|{challenge_nonce}|{decision}|"
 *   f"{scope or ''}|{purpose or ''}|{access_duration or ''}|{expires_at}"
 */
export function constructSigningInput(params: {
  request_id: string
  patient_id: string
  provider_id: string
  challenge_nonce: string
  decision: 'approved' | 'denied'
  scope: string
  purpose: string
  access_duration: number
  expires_at: string
}): string {
  return [
    params.request_id,
    params.patient_id,
    params.provider_id ?? '',
    params.challenge_nonce,
    params.decision,
    params.scope ?? '',
    params.purpose ?? '',
    params.access_duration ?? '',
    params.expires_at,
  ].join('|')
}

/**
 * Prompt for biometric confirmation (Face ID / Touch ID / fingerprint),
 * then sign the 9-attribute consent challenge with the device private key.
 *
 * The private key is ONLY accessed after successful biometric authentication.
 * Returns a base64 DER-encoded ECDSA signature.
 *
 * Throws if biometric hardware is unavailable/unenrolled, if the user
 * cancels/fails the prompt, or if no device key has been enrolled yet
 * (call enrollDeviceKey() first).
 */
export async function signConsentChallenge(params: {
  request_id: string
  patient_id: string
  provider_id: string
  challenge_nonce: string
  decision: 'approved' | 'denied'
  scope: string
  purpose: string
  access_duration: number
  expires_at: string
}): Promise<string> {
  const hasHardware = await LocalAuthentication.hasHardwareAsync()
  const isEnrolled = await LocalAuthentication.isEnrolledAsync()
  if (!hasHardware || !isEnrolled) {
    throw new Error('Biometric authentication not available on this device.')
  }

  const authResult = await LocalAuthentication.authenticateAsync({
    promptMessage: 'Confirm identity to approve record access',
    fallbackLabel: 'Use Passcode',
  })
  if (!authResult.success) {
    throw new Error('Biometric confirmation was not completed.')
  }

  const existing = await SecureStore.getItemAsync(PRIVATE_KEY_STORAGE_KEY, {
    requireAuthentication: true,
  })
  if (!existing) {
    throw new Error(
      'No device signing key found. Call enrollDeviceKey() once before requesting approval.'
    )
  }
  const privateKey = base64ToBytes(existing)

  // Construct the canonical 9-attribute signing input
  const message = constructSigningInput(params)
  const messageBytes = new TextEncoder().encode(message)

  // Hash with SHA-256: the backend verifies with ec.ECDSA(hashes.SHA256())
  const digest = new Uint8Array(
    await Crypto.digest(Crypto.CryptoDigestAlgorithm.SHA256, messageBytes)
  )
  const signature = p256.sign(digest, privateKey)

  // Python's cryptography library verifies DER-encoded ECDSA signatures
  return bytesToBase64(signature.toDERRawBytes())
}

/**
 * @deprecated Use signConsentChallenge() instead.
 * Legacy 3-field signing for biometric_signature_verifier.py.
 * Retained only for backward compatibility during transition.
 * The canonical contract is the 9-pipe format (signConsentChallenge).
 */
export async function signPushChallenge(params: {
  nonce: string
  requestId: string
  patientId: string
}): Promise<string> {
  // Delegate to the canonical 9-pipe signing.
  // Callers should migrate to signConsentChallenge() with full attributes.
  // This stub preserves the export name but the 3-field contract is
  // no longer supported — callers must provide full attributes.
  throw new Error(
    'signPushChallenge() is deprecated. Use signConsentChallenge() with the full 9-attribute signing input.'
  )
}
