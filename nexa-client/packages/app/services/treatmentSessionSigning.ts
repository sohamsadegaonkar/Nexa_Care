import {
  ApiError,
  NexaApiClient,
  type SignedTreatmentSessionV1Request,
  type SignedTreatmentSessionV1Response,
  type TreatmentSessionV1Challenge,
} from '../utils/apiClient'
import { authenticateWithBiometrics } from './deviceKeys'
import {
  CurrentDeviceError,
  ensureCurrentDeviceEnrollment,
  type CurrentDeviceEnrollment,
} from './currentDeviceEnrollment'
import { signWithNativeDeviceKey } from './nativeDeviceSecurity'

const REQUIRED_OPERATIONS = ['CREATE_ENCOUNTER', 'WRITE_VITALS'] as const

type TreatmentDecision = 'approved' | 'denied'

const pendingSignedDecisions = new Map<
  string,
  { decision: TreatmentDecision; payload: SignedTreatmentSessionV1Request }
>()

function exactOperations(challenge: TreatmentSessionV1Challenge): void {
  const operations = [...challenge.allowed_operations].sort()
  if (
    operations.length !== REQUIRED_OPERATIONS.length ||
    operations.some((value, index) => value !== REQUIRED_OPERATIONS[index])
  ) {
    throw new Error('TREATMENT_OPERATION_SET_UNSUPPORTED')
  }
}

export function constructTreatmentSessionSigningInput(
  challenge: TreatmentSessionV1Challenge,
  decision: TreatmentDecision,
  device: Pick<
    CurrentDeviceEnrollment,
    'deviceId' | 'keyId' | 'keyVersion' | 'keyFingerprint'
  >
): string {
  exactOperations(challenge)
  if (!/^[0-9a-f]{64}$/.test(challenge.provider_session_binding_hash)) {
    throw new Error('TREATMENT_PROVIDER_SESSION_BINDING_INVALID')
  }
  if (!/^[0-9a-f]{64}$/.test(challenge.treatment_context_hash)) {
    throw new Error('TREATMENT_CONTEXT_HASH_INVALID')
  }

  return JSON.stringify({
    access_duration: challenge.access_duration,
    allowed_operations: [...challenge.allowed_operations].sort(),
    challenge_nonce: challenge.challenge_nonce,
    decision,
    device_id: device.deviceId,
    domain: 'NEXA_CARE_SIGNED_TREATMENT_SESSION',
    expires_at: challenge.expires_at,
    hospital_id: challenge.hospital_id,
    issued_at: challenge.issued_at,
    key_id: device.keyId,
    key_version: device.keyVersion,
    operation: 'TREATMENT_SESSION_DECISION',
    patient_id: challenge.patient_id,
    policy_version: 'clinical-access-v1',
    protocol_version: 'nexa-treatment-session-v1',
    provider_id: challenge.provider_id,
    provider_session_binding_hash: challenge.provider_session_binding_hash,
    public_key_fingerprint: device.keyFingerprint,
    purpose: challenge.purpose,
    request_id: challenge.request_id,
    treatment_context_hash: challenge.treatment_context_hash,
  })
}

async function signedPayload(
  challenge: TreatmentSessionV1Challenge,
  decision: TreatmentDecision,
  device: CurrentDeviceEnrollment
): Promise<SignedTreatmentSessionV1Request> {
  const cached = pendingSignedDecisions.get(challenge.request_id)
  if (cached?.decision === decision) return cached.payload
  if (cached) pendingSignedDecisions.delete(challenge.request_id)

  const input = constructTreatmentSessionSigningInput(challenge, decision, device)
  const signature = await signWithNativeDeviceKey(device.keyAlias, input)
  const payload: SignedTreatmentSessionV1Request = {
    protocol_version: 'nexa-treatment-session-v1',
    request_id: challenge.request_id,
    patient_id: challenge.patient_id,
    decision,
    challenge_nonce: challenge.challenge_nonce,
    treatment_context_hash: challenge.treatment_context_hash,
    signature,
    device_id: device.deviceId,
    key_id: device.keyId,
    key_version: device.keyVersion,
    public_key_fingerprint: device.keyFingerprint,
  }
  pendingSignedDecisions.set(challenge.request_id, { decision, payload })
  return payload
}

async function submitDecision(
  challenge: TreatmentSessionV1Challenge,
  decision: TreatmentDecision,
  requireBiometric: boolean
): Promise<SignedTreatmentSessionV1Response> {
  exactOperations(challenge)
  const device = await ensureCurrentDeviceEnrollment({ allowEnrollment: false })
  if (requireBiometric && !pendingSignedDecisions.has(challenge.request_id)) {
    await authenticateWithBiometrics()
  }
  const payload = await signedPayload(challenge, decision, device)
  try {
    const response = await NexaApiClient.submitSignedTreatmentSessionV1(payload)
    pendingSignedDecisions.delete(challenge.request_id)
    return response
  } catch (error) {
    if (
      error instanceof ApiError &&
      error.status > 0 &&
      error.status < 500 &&
      error.code !== 'REQUEST_TIMEOUT'
    ) {
      pendingSignedDecisions.delete(challenge.request_id)
    }
    throw error
  }
}

export function approveTreatmentSessionWithBiometric(
  challenge: TreatmentSessionV1Challenge
): Promise<SignedTreatmentSessionV1Response> {
  return submitDecision(challenge, 'approved', true)
}

export function denyTreatmentSessionWithSignature(
  challenge: TreatmentSessionV1Challenge
): Promise<SignedTreatmentSessionV1Response> {
  return submitDecision(challenge, 'denied', false)
}

export function fetchTreatmentSessionChallenge(
  requestId: string
): Promise<TreatmentSessionV1Challenge> {
  return NexaApiClient.fetchTreatmentSessionV1Challenge(requestId)
}

export function isTreatmentSessionChallengeExpired(
  challenge: TreatmentSessionV1Challenge
): boolean {
  const expiresAt = Date.parse(challenge.expires_at)
  return Number.isNaN(expiresAt) || Date.now() >= expiresAt
}

export type TreatmentSessionApprovalErrorKind =
  | 'reauth'
  | 'forbidden'
  | 'expired'
  | 'setup'
  | 'retry'

export function classifyTreatmentSessionApprovalError(error: unknown): {
  kind: TreatmentSessionApprovalErrorKind
  message: string
} {
  if (error instanceof CurrentDeviceError) {
    if (error.code === 'REAUTH_REQUIRED') {
      return { kind: 'reauth', message: 'Your patient session expired. Sign in again.' }
    }
    return { kind: 'setup', message: error.message }
  }
  if (error instanceof ApiError) {
    if (error.status === 401) {
      return { kind: 'reauth', message: 'Your patient session expired. Sign in again.' }
    }
    if (
      error.code === 'TREATMENT_CHALLENGE_EXPIRED' ||
      error.code === 'TREATMENT_CHALLENGE_NOT_FOUND'
    ) {
      return { kind: 'expired', message: 'This treatment request is no longer active.' }
    }
    if (error.status === 403) {
      return {
        kind: 'forbidden',
        message: 'This treatment request is not authorized for the signed-in patient.',
      }
    }
  }
  return {
    kind: 'retry',
    message: 'Treatment approval could not be confirmed. Retry safely.',
  }
}

export function clearTreatmentSessionSignedRetry(requestId: string): void {
  pendingSignedDecisions.delete(requestId)
}
