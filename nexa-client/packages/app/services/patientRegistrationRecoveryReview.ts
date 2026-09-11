import { apiClient, ApiError } from '../utils/apiClient'
import {
  RegistrationRecoveryReviewNextAction,
  RegistrationRecoveryReviewStatus,
} from './patientRegistrationRecovery'

export type {
  RegistrationRecoveryReviewNextAction,
  RegistrationRecoveryReviewStatus,
}

export type RegistrationRecoveryReviewOutcome =
  | 'RESTORE_MISSING_RECORD_ANCHOR'
  | 'REBIND_MERGED_IDENTITY'
  | 'NO_REPAIR'
  | 'SECURITY_ESCALATION_REQUIRED'

export type RegistrationRecoveryReviewReason =
  | 'MISSING_RECORD_ANCHOR'
  | 'MERGED_IDENTITY_REBIND_REQUIRED'
  | 'IDENTITY_REVOKED'
  | 'PATIENT_DELETED_WITHOUT_MERGE'
  | 'ERASURE_STATE_PRESENT'
  | 'MULTIPLE_IDENTITIES'
  | 'MERGE_AMBIGUOUS'
  | 'GRAPH_STATE_CHANGED'
  | 'SECURITY_CONCERN'

export interface ReviewerCaseResponse {
  case_reference: string
  patient_id: string | null
  status: RegistrationRecoveryReviewStatus
  reason_codes: RegistrationRecoveryReviewReason[]
  version: number
  assigned_to_current_reviewer: boolean
  created_at: string
  claimed_at: string | null
  resolved_at: string | null
  contract_version: string
  policy_version: string
  outcome?: RegistrationRecoveryReviewOutcome
}

export interface ReviewerCaseListResponse {
  cases: ReviewerCaseResponse[]
}

export interface ReviewerMutationRequest {
  expected_version: number
}

export interface ReviewerResolveRequest extends ReviewerMutationRequest {
  idempotency_key: string
  outcome: RegistrationRecoveryReviewOutcome
  reason_codes: RegistrationRecoveryReviewReason[]
}

export interface ReviewerResolveResponse extends ReviewerCaseResponse {
  outcome: RegistrationRecoveryReviewOutcome
}

export class RegistrationRecoveryReviewClientError extends Error {
  constructor(
    message: string,
    public readonly code: string,
    public readonly httpStatus: number,
    public readonly retryable: boolean,
    public readonly details?: unknown
  ) {
    super(message)
    this.name = 'RegistrationRecoveryReviewClientError'
  }
}

function mapReviewerError(error: unknown): RegistrationRecoveryReviewClientError {
  if (error instanceof RegistrationRecoveryReviewClientError) return error
  if (error instanceof ApiError) {
    const code = error.code ?? 'REGISTRATION_RECOVERY_REVIEW_UNKNOWN'
    return new RegistrationRecoveryReviewClientError(
      error.message || 'Reviewer operation failed',
      code,
      error.status,
      error.isRetryable,
      error.details
    )
  }
  return new RegistrationRecoveryReviewClientError(
    error instanceof Error ? error.message : 'Reviewer operation failed',
    'UNKNOWN',
    0,
    false
  )
}

export async function listReviewerCases(params?: {
  status?: RegistrationRecoveryReviewStatus
  limit?: number
}): Promise<ReviewerCaseListResponse> {
  try {
    const searchParams = new URLSearchParams()
    if (params?.status) searchParams.set('status', params.status)
    if (params?.limit) searchParams.set('limit', String(params.limit))
    const query = searchParams.toString() ? `?${searchParams.toString()}` : ''

    const { data } = await apiClient.get<ReviewerCaseListResponse>(
      `/api/v2/auth/registration-recovery/review/reviewer/cases${query}`
    )
    return data
  } catch (error) {
    throw mapReviewerError(error)
  }
}

export async function getReviewerCase(caseReference: string): Promise<ReviewerCaseResponse> {
  const normalized = caseReference?.trim()
  if (!normalized) {
    throw new RegistrationRecoveryReviewClientError(
      'Case reference is required',
      'CASE_REFERENCE_REQUIRED',
      400,
      false
    )
  }

  try {
    const { data } = await apiClient.get<ReviewerCaseResponse>(
      `/api/v2/auth/registration-recovery/review/reviewer/cases/${encodeURIComponent(normalized)}`
    )
    return data
  } catch (error) {
    throw mapReviewerError(error)
  }
}

export async function claimReviewerCase(
  caseReference: string,
  expectedVersion: number
): Promise<ReviewerCaseResponse> {
  const normalized = caseReference?.trim()
  if (!normalized) {
    throw new RegistrationRecoveryReviewClientError(
      'Case reference is required',
      'CASE_REFERENCE_REQUIRED',
      400,
      false
    )
  }

  try {
    const payload: ReviewerMutationRequest = { expected_version: expectedVersion }
    const { data } = await apiClient.post<ReviewerCaseResponse>(
      `/api/v2/auth/registration-recovery/review/reviewer/cases/${encodeURIComponent(normalized)}/claim`,
      payload
    )
    return data
  } catch (error) {
    throw mapReviewerError(error)
  }
}

export async function recoverReviewerSession(
  caseReference: string,
  expectedVersion: number
): Promise<ReviewerCaseResponse> {
  const normalized = caseReference?.trim()
  if (!normalized) {
    throw new RegistrationRecoveryReviewClientError(
      'Case reference is required',
      'CASE_REFERENCE_REQUIRED',
      400,
      false
    )
  }

  try {
    const payload: ReviewerMutationRequest = { expected_version: expectedVersion }
    const { data } = await apiClient.post<ReviewerCaseResponse>(
      `/api/v2/auth/registration-recovery/review/reviewer/cases/${encodeURIComponent(normalized)}/recover-session`,
      payload
    )
    return data
  } catch (error) {
    throw mapReviewerError(error)
  }
}

export async function resolveReviewerCase(
  caseReference: string,
  params: {
    expectedVersion: number
    idempotencyKey: string
    outcome: RegistrationRecoveryReviewOutcome
    reasonCodes: RegistrationRecoveryReviewReason[]
  }
): Promise<ReviewerResolveResponse> {
  const normalized = caseReference?.trim()
  if (!normalized) {
    throw new RegistrationRecoveryReviewClientError(
      'Case reference is required',
      'CASE_REFERENCE_REQUIRED',
      400,
      false
    )
  }

  try {
    const payload: ReviewerResolveRequest = {
      expected_version: params.expectedVersion,
      idempotency_key: params.idempotencyKey,
      outcome: params.outcome,
      reason_codes: params.reasonCodes,
    }
    const { data } = await apiClient.post<ReviewerResolveResponse>(
      `/api/v2/auth/registration-recovery/review/reviewer/cases/${encodeURIComponent(normalized)}/resolve`,
      payload
    )
    return data
  } catch (error) {
    throw mapReviewerError(error)
  }
}
