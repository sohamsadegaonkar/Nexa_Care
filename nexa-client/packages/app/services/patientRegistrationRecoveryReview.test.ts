import { beforeEach, describe, expect, it, vi } from 'vitest'

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))

vi.mock('../utils/apiClient', () => ({
  ApiError: class ApiError extends Error {
    status = 0
    code: string | undefined
    isRetryable = false
    details: unknown = undefined
    constructor(
      message: string,
      status: number,
      code?: string,
      isRetryable = false,
      details?: unknown
    ) {
      super(message)
      this.status = status
      this.code = code
      this.isRetryable = isRetryable
      this.details = details
    }
  },
  apiClient: { get, post },
}))

import {
  RegistrationRecoveryReviewClientError,
  claimReviewerCase,
  getReviewerCase,
  listReviewerCases,
  recoverReviewerSession,
  resolveReviewerCase,
} from './patientRegistrationRecoveryReview'

describe('patient registration recovery reviewer service', () => {
  beforeEach(() => {
    vi.resetAllMocks()
  })

  it('lists reviewer cases with status filter and limit', async () => {
    get.mockResolvedValueOnce({
      data: {
        cases: [
          {
            case_reference: 'RRC-123456789012345678',
            patient_id: null,
            status: 'PENDING',
            reason_codes: ['MISSING_RECORD_ANCHOR'],
            version: 1,
            assigned_to_current_reviewer: false,
            created_at: '2026-09-11T10:00:00+00:00',
            claimed_at: null,
            resolved_at: null,
            contract_version: 'registration-recovery-review/1.0',
            policy_version: 'registration-recovery-review-policy/1.0',
          },
        ],
      },
    })

    const res = await listReviewerCases({ status: 'PENDING', limit: 20 })

    expect(get).toHaveBeenCalledWith(
      '/api/v2/auth/registration-recovery/review/reviewer/cases?status=PENDING&limit=20'
    )
    expect(res.cases).toHaveLength(1)
    expect(res.cases[0].case_reference).toBe('RRC-123456789012345678')
  })

  it('fetches single reviewer case by case reference', async () => {
    const mockCase = {
      case_reference: 'RRC-123456789012345678',
      patient_id: 'patient-uuid',
      status: 'IN_REVIEW',
      reason_codes: ['MERGED_IDENTITY_REBIND_REQUIRED'],
      version: 2,
      assigned_to_current_reviewer: true,
      created_at: '2026-09-11T10:00:00+00:00',
      claimed_at: '2026-09-11T10:02:00+00:00',
      resolved_at: null,
      contract_version: 'registration-recovery-review/1.0',
      policy_version: 'registration-recovery-review-policy/1.0',
    }
    get.mockResolvedValueOnce({ data: mockCase })

    const res = await getReviewerCase('RRC-123456789012345678')

    expect(get).toHaveBeenCalledWith(
      '/api/v2/auth/registration-recovery/review/reviewer/cases/RRC-123456789012345678'
    )
    expect(res.version).toBe(2)
  })

  it('claims case sending expected_version', async () => {
    post.mockResolvedValueOnce({
      data: {
        case_reference: 'RRC-123456789012345678',
        version: 2,
        status: 'IN_REVIEW',
        assigned_to_current_reviewer: true,
      },
    })

    const res = await claimReviewerCase('RRC-123456789012345678', 1)

    expect(post).toHaveBeenCalledWith(
      '/api/v2/auth/registration-recovery/review/reviewer/cases/RRC-123456789012345678/claim',
      { expected_version: 1 }
    )
    expect(res.version).toBe(2)
    expect(res.status).toBe('IN_REVIEW')
  })

  it('recovers session sending expected_version', async () => {
    post.mockResolvedValueOnce({
      data: {
        case_reference: 'RRC-123456789012345678',
        version: 3,
        status: 'IN_REVIEW',
        assigned_to_current_reviewer: true,
      },
    })

    const res = await recoverReviewerSession('RRC-123456789012345678', 2)

    expect(post).toHaveBeenCalledWith(
      '/api/v2/auth/registration-recovery/review/reviewer/cases/RRC-123456789012345678/recover-session',
      { expected_version: 2 }
    )
    expect(res.version).toBe(3)
  })

  it('resolves reviewer case with exact schema and preserved idempotency key', async () => {
    post.mockResolvedValueOnce({
      data: {
        case_reference: 'RRC-123456789012345678',
        version: 3,
        status: 'RESOLVED',
        outcome: 'RESTORE_MISSING_RECORD_ANCHOR',
        assigned_to_current_reviewer: true,
      },
    })

    const idempotencyKey = 'durable-key-12345678'
    const res = await resolveReviewerCase('RRC-123456789012345678', {
      expectedVersion: 2,
      idempotencyKey,
      outcome: 'RESTORE_MISSING_RECORD_ANCHOR',
      reasonCodes: ['MISSING_RECORD_ANCHOR'],
    })

    expect(post).toHaveBeenCalledWith(
      '/api/v2/auth/registration-recovery/review/reviewer/cases/RRC-123456789012345678/resolve',
      {
        expected_version: 2,
        idempotency_key: idempotencyKey,
        outcome: 'RESTORE_MISSING_RECORD_ANCHOR',
        reason_codes: ['MISSING_RECORD_ANCHOR'],
      }
    )
    expect(res.outcome).toBe('RESTORE_MISSING_RECORD_ANCHOR')
  })

  it('maps API conflict error (version conflict) to typed RegistrationRecoveryReviewClientError', async () => {
    const error = new (await import('../utils/apiClient')).ApiError(
      'Version conflict',
      409,
      'REGISTRATION_RECOVERY_REVIEW_VERSION_CONFLICT',
      false,
      { detail: { error_code: 'REGISTRATION_RECOVERY_REVIEW_VERSION_CONFLICT' } }
    )
    post.mockRejectedValueOnce(error)

    await expect(
      claimReviewerCase('RRC-123456789012345678', 1)
    ).rejects.toThrow(RegistrationRecoveryReviewClientError)
  })
})
