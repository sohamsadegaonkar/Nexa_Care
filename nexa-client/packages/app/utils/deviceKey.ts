/**
 * Device signing key management for push-approval biometric assurance.
 *
 * Backend contract (app/services/biometric_signature_verifier.py):
 *   - EC key on the P-256 curve (secp256r1)
 *   - Public key stored/sent as DER (X.509 SubjectPublicKeyInfo), base64
 *   - Signature: ECDSA-SHA256 over utf8(`${nonce}${requestId}${patientId}`)
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
 * expo-local-authentication immediately before each signing operation,
 * matching the flow already built in PatientApprovalScreen.tsx.
 *
 * Honest limitation vs. true hardware-backed signing: the private key is
 * briefly resident in JS memory during signing, unlike a key that never
 * leaves a Secure Enclave. It is still encrypted at rest by the OS
 * keystore, and biometric confirmation is still required to retrieve it.
 * If true never-leaves-hardware EC signing is required later, that means
 * a native module (e.g. a small Secure Enclave/StrongBox wrapper) --
 * scope that as its own task rather than assuming this covers it.
 *
 * NOT YET VERIFIED ON DEVICE: this file was written and syntax-checked in
 * a sandboxed environment with no ability to run a native Expo build.
 * Run it on a real iOS/Android device or simulator with biometrics
 * enrolled before relying on it for a demo.
 */

import * as SecureStore from 'expo-secure-store'
import { p256 } from '@noble/curves/p256'
import * as LocalAuthentication from 'expo-local-authentication'
import * as Crypto from 'expo-crypto'
import { registerDeviceKey } from '../api/assurance'

const PRIVATE_KEY_STORAGE_KEY = 'nexa_device_signing_private_key_v1'

function bytesToBase64(bytes: Uint8Array): string {
  let binary = ''
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i])
  // eslint-disable-next-line no-undef
  return typeof btoa === 'function' ? btoa(binary) : Buffer.from(bytes).toString('base64')
}

function base64ToBytes(b64: string): Uint8Array {
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
  // SubjectPublicKeyInfo ::= SEQUENCE {
  //   algorithm AlgorithmIdentifier { id-ecPublicKey, prime256v1 },
  //   subjectPublicKey BIT STRING }
  // This header is fixed for P-256 + id-ecPublicKey and is standard
  // across OpenSSL/cryptography-produced P-256 DER keys.
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

/**
 * One-time (per device) enrollment: generate a P-256 keypair, keep the
 * private key in the OS keystore, and register the public key with the
 * backend via POST /api/v2/push/register-device-key.
 *
 * Requires the patient to have already completed provider-led NFC
 * enrollment in person -- the backend will reject this call with 409 if
 * no active biometric_registry row exists yet for the patient.
 *
 * Call this once, e.g. on first login or from a "Security" settings
 * screen -- not on every approval.
 */
export async function enrollDeviceKey(): Promise<void> {
  const { publicKeyDer } = await loadOrCreateKeyPair()
  await registerDeviceKey({ public_key: bytesToBase64(publicKeyDer) })
}

/**
 * Prompts for biometric confirmation, then signs the push-approval
 * challenge with the device's private key. Returns a base64 ECDSA
 * signature ready to send as `signature` in respondToPushRequest().
 *
 * Throws if biometric hardware is unavailable/unenrolled, if the user
 * cancels/fails the prompt, or if no device key has been enrolled yet
 * (call enrollDeviceKey() first).
 */
export async function signPushChallenge(params: {
  nonce: string
  requestId: string
  patientId: string
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

  // Must match biometric_signature_verifier.py exactly:
  // message = f"{challenge_nonce}{request_id}{patient_id}".encode("utf-8")
  const message = new TextEncoder().encode(`${params.nonce}${params.requestId}${params.patientId}`)

  // Hash explicitly with SHA-256 first: the backend verifies with
  // ec.ECDSA(hashes.SHA256()), which signs/verifies over a SHA-256 digest
  // of the message, not the raw message bytes. Using expo-crypto rather
  // than crypto.subtle -- the latter is a browser API not reliably
  // present in React Native/Hermes.
  const digest = new Uint8Array(
    await Crypto.digest(Crypto.CryptoDigestAlgorithm.SHA256, message)
  )
  const signature = p256.sign(digest, privateKey)

  // Python's `cryptography` library verifies DER-encoded ECDSA signatures
  // by default (public_key.verify(signature, ...)) -- send DER, not the
  // compact r||s form.
  return bytesToBase64(signature.toDERRawBytes())
}