import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  bindAdjudicationWorkflow,
  clearAllAdjudicationWorkflows,
  createIdempotencyKey,
  createReviewSessionId,
  getAdjudicationWorkflow,
  prepareAdjudicationMutation,
} from './adjudicationWorkflowStore'

describe('memory-only adjudication workflow state', () => {
  afterEach(() => {
    clearAllAdjudicationWorkflows()
    vi.restoreAllMocks()
  })

  it('generates collision-resistant session and idempotency identifiers', () => {
    const randomUUID = vi
      .spyOn(globalThis.crypto, 'randomUUID')
      .mockReturnValueOnce('00000000-0000-4000-8000-000000000001')
      .mockReturnValueOnce('00000000-0000-4000-8000-000000000002')
    expect(createReviewSessionId()).toBe('review:00000000-0000-4000-8000-000000000001')
    expect(createIdempotencyKey()).toBe('request:00000000-0000-4000-8000-000000000002')
    expect(randomUUID).toHaveBeenCalledTimes(2)
  })

  it('retains an idempotency key only for an identical retry', () => {
    bindAdjudicationWorkflow('case-1', 'review:session-1')
    const first = prepareAdjudicationMutation('case-1', 'same-content')
    const retry = prepareAdjudicationMutation('case-1', 'same-content')
    const changed = prepareAdjudicationMutation('case-1', 'changed-content')
    expect(retry).toBe(first)
    expect(changed).not.toBe(first)
  })

  it('has no durable recovery after process-memory state is cleared', () => {
    bindAdjudicationWorkflow('case-1', 'review:session-1')
    expect(getAdjudicationWorkflow('case-1')).not.toBeNull()
    clearAllAdjudicationWorkflows()
    expect(getAdjudicationWorkflow('case-1')).toBeNull()
  })
})
