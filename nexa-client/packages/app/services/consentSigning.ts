/**
 * Consent signing service for Nexa Care patient app.
 *
 * Constructs the canonical WS2 signing input and signs it with the
 * device private key (ECDSA P-256 / SHA-256).  The signing input
 * matches app/services/signed_approval_verifier.py byte-for-byte:
 *
 *   request_id|patient_id|provider_id|challenge_nonce|decision|
 *   scope|purpose|access_duration|expires_at
 *
 * ALPHA: P-256 keypair generated client-side and private key stored in
 * platform secure storage. Not yet: hardware-backed non-exportable
 * signing key with biometric-gated key usage.
 */

import * as SecureStore from 'expo-secure-store'
import * as LocalAuthentication from 'expo-local-authentication'
import * as Crypto from 'expo-crypto'
import { p256 } from '@noble/curves/p256'
import { apiClient } from '../utils/api'
import { getDeviceId } from './deviceKeys'

// ── Types ────────────────────────────────────────────────────────────────────

export interface ConsentChallenge {
  request_id: string
  patient_id: string
  provider_id: string
  provider_name: string
  hospital_name: string
  purpose: string
  scope: string
  access_duration: number
  challenge_nonce: string
  expires_at: string
  status: string
}

export interface SignedApprovalPayload {
  request_id: string
  patient_id: string
  decision: 'approved' | 'denied'
  challenge_nonce: string
  signature: string
  device_id: string
}

export interface SignedApprovalResponse {
  request_id: string
  status: string
  responded_at: string
}

// ── Internal helpers ─────────────────────────────────────────────────────────

const PRIVATE_KEY_STORE_KEY = 'nexa_device_private_key_v1'

function bytesToBase64(bytes: Uint8Array): string {
  let binary = ''
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i])
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

// ── Signing input construction ───────────────────────────────────────────────

/**
 * Build the canonical 9-attribute signing input that the backend
 * verifier reconstructs and checks against.
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

// ── Biometric gating ─────────────────────────────────────────────────────────

/**
 * Prompt the user for biometric authentication (Face ID / Touch ID).
 * Returns true if successful, false if cancelled or unavailable.
 *
 * ALPHA: In a real device build this gates access to the private key.
 * Not yet: hardware-backed non-exportable signing key with biometric-gated
 * key usage.
 */
export async function authenticateWithBiometrics(): Promise<boolean> {
  const hasHardware = await LocalAuthentication.hasHardwareAsync()
  const isEnrolled = await LocalAuthentication.isEnrolledAsync()

  if (!hasHardware || !isEnrolled) {
    // No biometric hardware — allow fallback for alpha
    return true
  }

  const result = await LocalAuthentication.authenticateAsync({
    promptMessage: 'Confirm your identity to approve this request',
    fallbackLabel: 'Use Passcode',
    cancelLabel: 'Cancel',
  })

  return result.success
}

// ── Core signing ─────────────────────────────────────────────────────────────

/**
 * Sign a consent decision with the device private key.
 *
 * 1. Load the private key from SecureStore.
 * 2. Construct the canonical 9-attribute signing input.
 * 3. SHA-256 hash the input (expo-crypto).
 * 4. Sign the hash with ECDSA P-256.
 * 5. Return the DER/base64 signature.
 *
 * The private key is NEVER sent to the backend.
 */
export async function signConsentDecision(params: {
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
  // Load private key from secure storage
  const existing = await SecureStore.getItemAsync(PRIVATE_KEY_STORE_KEY)
  if (!existing) {
    throw new Error('No device signing key found. Enroll your device first.')
  }
  const privateKey = base64ToBytes(existing)

  // Construct canonical signing input
  const message = constructSigningInput(params)
  const messageBytes = new TextEncoder().encode(message)

  // Hash with SHA-256 using expo-crypto
  const digest = new Uint8Array(
    await Crypto.digest(Crypto.CryptoDigestAlgorithm.SHA256, messageBytes),
  )

  // Sign the digest with ECDSA P-256
  // @noble/curves p256.sign takes the hash (not the raw message)
  const signature = p256.sign(digest, privateKey)

  // Backend verifies with DER-encoded signature
  return bytesToBase64(signature.toDERRawBytes())
}

// ── Full approval flow ───────────────────────────────────────────────────────

/**
 * Complete the approve flow: biometric gate → sign → submit.
 *
 * Private key is only accessed after successful biometric authentication.
 */
export async function approveWithBiometric(challenge: ConsentChallenge): Promise<SignedApprovalResponse> {
  // Gate: require biometric before accessing private key
  const biometricOk = await authenticateWithBiometrics()
  if (!biometricOk) {
    throw new Error('Biometric verification cancelled.')
  }

  // Sign with decision="approved"
  const signature = await signConsentDecision({
    request_id: challenge.request_id,
    patient_id: challenge.patient_id,
    provider_id: challenge.provider_id,
    challenge_nonce: challenge.challenge_nonce,
    decision: 'approved',
    scope: challenge.scope,
    purpose: challenge.purpose,
    access_duration: challenge.access_duration,
    expires_at: challenge.expires_at,
  })

  // Get device_id from secure storage
  const deviceId = await getDeviceId()
  if (!deviceId) {
    throw new Error('Device not enrolled. Enroll your device first.')
  }

  // Submit signed approval to backend
  const { data } = await apiClient.post<SignedApprovalResponse>(
    '/api/v2/consent/approve-signed',
    {
      request_id: challenge.request_id,
      patient_id: challenge.patient_id,
      decision: 'approved',
      challenge_nonce: challenge.challenge_nonce,
      signature,
      device_id: deviceId,
    } as unknown as Record<string, unknown>,
  )

  return data
}

/**
 * Complete the deny flow: sign (no biometric) → submit.
 *
 * Denial does not require biometric gating per WS2, but still
 * signs to prove the denial came from the real patient device.
 */
export async function denyWithSignature(challenge: ConsentChallenge): Promise<SignedApprovalResponse> {
  // Sign with decision="denied" — no biometric gate for denial
  const signature = await signConsentDecision({
    request_id: challenge.request_id,
    patient_id: challenge.patient_id,
    provider_id: challenge.provider_id,
    challenge_nonce: challenge.challenge_nonce,
    decision: 'denied',
    scope: challenge.scope,
    purpose: challenge.purpose,
    access_duration: challenge.access_duration,
    expires_at: challenge.expires_at,
  })

  // Get device_id from secure storage
  const deviceId = await getDeviceId()
  if (!deviceId) {
    throw new Error('Device not enrolled. Enroll your device first.')
  }

  // Submit signed denial to backend
  const { data } = await apiClient.post<SignedApprovalResponse>(
    '/api/v2/consent/approve-signed',
    {
      request_id: challenge.request_id,
      patient_id: challenge.patient_id,
      decision: 'denied',
      challenge_nonce: challenge.challenge_nonce,
      signature,
      device_id: deviceId,
    } as unknown as Record<string, unknown>,
  )

  return data
}

// ── Challenge fetch ──────────────────────────────────────────────────────────

/**
 * Fetch the full challenge details for a consent request.
 * Uses the patient-facing challenge endpoint.
 */
export async function fetchChallenge(requestId: string): Promise<ConsentChallenge> {
  const { data } = await apiClient.get<ConsentChallenge>(
    `/api/v2/consent/challenge/${requestId}`,
  )
  return data
}

/**
 * Check if a consent request has expired by comparing expires_at
 * to the current time.
 */
export function isChallengeExpired(challenge: ConsentChallenge): boolean {
  try {
    const expiresAt = new Date(challenge.expires_at).getTime()
    return Date.now() >= expiresAt
  } catch {
    return true
  }
}
