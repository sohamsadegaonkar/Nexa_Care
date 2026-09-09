/** Canonical Signed Consent V3 orchestration. Private-key operations use native key handles. */
import { ApiError, NexaApiClient } from '../utils/apiClient'
import {
  authenticateWithBiometrics as requireBiometrics,
  constructConsentSigningInputV3,
} from './deviceKeys'
import { CurrentDeviceError, ensureCurrentDeviceEnrollment } from './currentDeviceEnrollment'
import { signWithNativeDeviceKey } from './nativeDeviceSecurity'

export const constructSigningInput = constructConsentSigningInputV3
export async function authenticateWithBiometrics(): Promise<boolean> {
  await requireBiometrics()
  return true
}

export interface ConsentChallenge {
  protocol_version: 'nexa-consent-v3'
  request_id: string
  patient_id: string
  provider_id: string
  hospital_id: string
  provider_name: string
  hospital_name: string
  purpose: string
  scope: string
  access_duration: number
  challenge_nonce: string
  issued_at: string
  expires_at: string
  consent_context_hash: string
  status: string
}
export interface SignedApprovalResponse {
  request_id: string
  status: string
  responded_at: string
}

/**
 * The decision-signing seam intentionally accepts a native key alias, never raw private-key
 * material. Keeping this named seam also makes the end-to-end consent guardrail explicit.
 */
export async function signConsentDecision(keyAlias: string, signingInput: string): Promise<string> {
  return signWithNativeDeviceKey(keyAlias, signingInput)
}

async function submitSignedDecision(
  challenge: ConsentChallenge,
  decision: 'approved' | 'denied',
  device: {
    deviceId: string
    keyId: string
    keyVersion: number
    keyFingerprint: string
    keyAlias: string
  }
): Promise<SignedApprovalResponse> {
  const signingInput = constructSigningInput({
    request_id: challenge.request_id,
    patient_id: challenge.patient_id,
    provider_id: challenge.provider_id,
    hospital_id: challenge.hospital_id,
    challenge_nonce: challenge.challenge_nonce,
    decision,
    scope: challenge.scope,
    purpose: challenge.purpose,
    access_duration: challenge.access_duration,
    issued_at: challenge.issued_at,
    expires_at: challenge.expires_at,
    consent_context_hash: challenge.consent_context_hash,
    device_id: device.deviceId,
    key_id: device.keyId,
    key_version: device.keyVersion,
    public_key_fingerprint: device.keyFingerprint,
  })
  const signature = await signConsentDecision(device.keyAlias, signingInput)
  const payload = {
    protocol_version: 'nexa-consent-v3' as const,
    request_id: challenge.request_id,
    patient_id: challenge.patient_id,
    decision,
    challenge_nonce: challenge.challenge_nonce,
    consent_context_hash: challenge.consent_context_hash,
    signature,
    device_id: device.deviceId,
    key_id: device.keyId,
    key_version: device.keyVersion,
    public_key_fingerprint: device.keyFingerprint,
  }
  // NexaApiClient.approveSignedConsent is the canonical transport for
  // /api/v2/consent/v3/approve-signed; denial uses the corresponding V3 deny transport.
  return decision === 'approved'
    ? NexaApiClient.approveSignedConsent(payload)
    : NexaApiClient.denySignedConsent(payload)
}

export async function approveWithBiometric(
  challenge: ConsentChallenge
): Promise<SignedApprovalResponse> {
  const currentDevice = await ensureCurrentDeviceEnrollment({ allowEnrollment: false })
  await requireBiometrics()
  return submitSignedDecision(challenge, 'approved', currentDevice)
}

export type ConsentErrorKind = 'reauth' | 'forbidden' | 'not-found' | 'expired' | 'setup' | 'retry'

export function classifyConsentError(error: unknown): { kind: ConsentErrorKind; message: string } {
  if (error instanceof CurrentDeviceError) {
    return error.code === 'REAUTH_REQUIRED'
      ? {
          kind: 'reauth',
          message: 'Your session expired. Sign in with OTP again to secure this device.',
        }
      : { kind: 'setup', message: error.message }
  }
  if (error instanceof ApiError) {
    if (error.status === 401)
      return { kind: 'reauth', message: 'Your session expired. Sign in again.' }
    if (error.status === 403)
      return {
        kind: 'forbidden',
        message: 'This request does not belong to the signed-in patient.',
      }
    if (error.status === 404)
      return { kind: 'not-found', message: 'This consent request was not found.' }
    if (error.status === 410)
      return { kind: 'expired', message: 'This consent request has expired.' }
  }
  return { kind: 'retry', message: 'Unable to load this consent request. Please retry.' }
}

export async function denyWithSignature(
  challenge: ConsentChallenge
): Promise<SignedApprovalResponse> {
  const currentDevice = await ensureCurrentDeviceEnrollment({ allowEnrollment: false })
  return submitSignedDecision(challenge, 'denied', currentDevice)
}

export async function fetchChallenge(requestId: string): Promise<ConsentChallenge> {
  return NexaApiClient.fetchConsentChallenge(requestId)
}

export function isChallengeExpired(challenge: ConsentChallenge): boolean {
  const expiresAt = Date.parse(challenge.expires_at)
  return Number.isNaN(expiresAt) || Date.now() >= expiresAt
}
