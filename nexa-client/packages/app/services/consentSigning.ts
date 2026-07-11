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

import { apiClient } from '../utils/apiClient'
import { authenticateWithBiometrics as requireBiometrics, constructConsentSigningInput, getDeviceId, signConsentChallenge } from './deviceKeys'

export const constructSigningInput = constructConsentSigningInput
export const signConsentDecision = signConsentChallenge
export async function authenticateWithBiometrics(): Promise<boolean> { await requireBiometrics(); return true }

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
