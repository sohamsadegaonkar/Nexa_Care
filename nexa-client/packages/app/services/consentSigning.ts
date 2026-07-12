/** Canonical consent challenge orchestration; key operations live only in deviceKeys.ts. */
import { NexaApiClient } from '../utils/apiClient'
import {
  authenticateWithBiometrics as requireBiometrics,
  constructConsentSigningInput,
  getDeviceId,
  signConsentChallenge,
} from './deviceKeys'

export const constructSigningInput = constructConsentSigningInput
export const signConsentDecision = signConsentChallenge
export async function authenticateWithBiometrics(): Promise<boolean> {
  await requireBiometrics()
  return true
}

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
export interface SignedApprovalResponse {
  request_id: string
  status: string
  responded_at: string
}

async function submitSignedDecision(
  challenge: ConsentChallenge,
  decision: 'approved' | 'denied',
): Promise<SignedApprovalResponse> {
  const deviceId = await getDeviceId()
  if (!deviceId) {
    throw new Error('This device is not enrolled. Please secure this device before approving consent.')
  }
  const signature = await signConsentDecision({
    request_id: challenge.request_id,
    patient_id: challenge.patient_id,
    provider_id: challenge.provider_id,
    challenge_nonce: challenge.challenge_nonce,
    decision,
    scope: challenge.scope,
    purpose: challenge.purpose,
    access_duration: challenge.access_duration,
    expires_at: challenge.expires_at,
  })
  const payload = {
    request_id: challenge.request_id,
    patient_id: challenge.patient_id,
    decision,
    challenge_nonce: challenge.challenge_nonce,
    signature,
    device_id: deviceId,
  }
  return decision === 'approved'
    ? NexaApiClient.approveSignedConsent(payload)
    : NexaApiClient.denySignedConsent(payload)
}

export async function approveWithBiometric(challenge: ConsentChallenge): Promise<SignedApprovalResponse> {
  await requireBiometrics()
  return submitSignedDecision(challenge, 'approved')
}

export async function denyWithSignature(challenge: ConsentChallenge): Promise<SignedApprovalResponse> {
  return submitSignedDecision(challenge, 'denied')
}

export async function fetchChallenge(requestId: string): Promise<ConsentChallenge> {
  return NexaApiClient.fetchConsentChallenge(requestId)
}

export function isChallengeExpired(challenge: ConsentChallenge): boolean {
  const expiresAt = Date.parse(challenge.expires_at)
  return Number.isNaN(expiresAt) || Date.now() >= expiresAt
}
