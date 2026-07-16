/** Canonical consent challenge orchestration; key operations live only in deviceKeys.ts. */
import { ApiError, NexaApiClient } from '../utils/apiClient'
import {
  authenticateWithBiometrics as requireBiometrics,
  constructConsentSigningInput,
  signConsentChallenge,
} from './deviceKeys'
import { CurrentDeviceError, ensureCurrentDeviceEnrollment } from './currentDeviceEnrollment'

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
  deviceId: string,
): Promise<SignedApprovalResponse> {
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
  // Confirm the exact local device before showing Android biometrics.
  const currentDevice = await ensureCurrentDeviceEnrollment({ allowEnrollment: false })
  await requireBiometrics()
  return submitSignedDecision(challenge, 'approved', currentDevice.deviceId)
}

export type ConsentErrorKind = 'reauth' | 'forbidden' | 'not-found' | 'expired' | 'setup' | 'retry'

export function classifyConsentError(error: unknown): { kind: ConsentErrorKind; message: string } {
  if (error instanceof CurrentDeviceError) {
    return error.code === 'REAUTH_REQUIRED'
      ? { kind: 'reauth', message: 'Your session expired. Sign in with OTP again to secure this device.' }
      : { kind: 'setup', message: error.message }
  }
  if (error instanceof ApiError) {
    if (error.status === 401) return { kind: 'reauth', message: 'Your session expired. Sign in again.' }
    if (error.status === 403) return { kind: 'forbidden', message: 'This request does not belong to the signed-in patient.' }
    if (error.status === 404) return { kind: 'not-found', message: 'This consent request was not found.' }
    if (error.status === 410) return { kind: 'expired', message: 'This consent request has expired.' }
  }
  return { kind: 'retry', message: 'Unable to load this consent request. Please retry.' }
}

export async function denyWithSignature(challenge: ConsentChallenge): Promise<SignedApprovalResponse> {
  const currentDevice = await ensureCurrentDeviceEnrollment({ allowEnrollment: false })
  return submitSignedDecision(challenge, 'denied', currentDevice.deviceId)
}

export async function fetchChallenge(requestId: string): Promise<ConsentChallenge> {
  return NexaApiClient.fetchConsentChallenge(requestId)
}

export function isChallengeExpired(challenge: ConsentChallenge): boolean {
  const expiresAt = Date.parse(challenge.expires_at)
  return Number.isNaN(expiresAt) || Date.now() >= expiresAt
}
