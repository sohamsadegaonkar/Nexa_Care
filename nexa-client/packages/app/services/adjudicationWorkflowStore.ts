/**
 * Process-memory-only state for a clinician source-adjudication workflow.
 *
 * Review sessions and mutation idempotency keys are never persisted, logged,
 * placed in navigation state, or embedded in URLs. A browser refresh therefore
 * loses this state and must fail closed.
 */

import { useSyncExternalStore } from 'react'
import type { AdjudicationOutcome, AdjudicationSubmissionResponse } from '../utils/apiClient'

type PendingMutation = {
  fingerprint: string
  idempotencyKey: string
}

export type AdjudicationWorkflow = {
  caseId: string
  reviewSessionId: string
  submission: AdjudicationSubmissionResponse | null
  committedAt: string | null
  pendingMutation: PendingMutation | null
}

type Listener = () => void

const workflows = new Map<string, AdjudicationWorkflow>()
const listeners = new Set<Listener>()

function notify(): void {
  for (const listener of listeners) listener()
}

function secureIdentifier(prefix: 'review' | 'request'): string {
  const randomUUID = globalThis.crypto?.randomUUID
  if (!randomUUID) {
    throw new Error('Secure browser randomness is unavailable.')
  }
  return `${prefix}:${randomUUID.call(globalThis.crypto)}`
}

export function createReviewSessionId(): string {
  return secureIdentifier('review')
}

export function createIdempotencyKey(): string {
  return secureIdentifier('request')
}

export function bindAdjudicationWorkflow(caseId: string, reviewSessionId: string): void {
  workflows.set(caseId, {
    caseId,
    reviewSessionId,
    submission: null,
    committedAt: null,
    pendingMutation: null,
  })
  notify()
}

export function getAdjudicationWorkflow(
  caseId: string | null | undefined
): AdjudicationWorkflow | null {
  if (!caseId) return null
  return workflows.get(caseId) ?? null
}

export function prepareAdjudicationMutation(caseId: string, fingerprint: string): string {
  const workflow = workflows.get(caseId)
  if (!workflow) throw new Error('Adjudication review session is unavailable.')
  if (workflow.pendingMutation?.fingerprint === fingerprint) {
    return workflow.pendingMutation.idempotencyKey
  }
  const idempotencyKey = createIdempotencyKey()
  workflows.set(caseId, {
    ...workflow,
    pendingMutation: { fingerprint, idempotencyKey },
  })
  notify()
  return idempotencyKey
}

export function recordAdjudicationSubmission(
  caseId: string,
  submission: AdjudicationSubmissionResponse
): void {
  const workflow = workflows.get(caseId)
  if (!workflow) return
  workflows.set(caseId, { ...workflow, submission, pendingMutation: null })
  notify()
}

export function recordAdjudicationCommit(caseId: string, committedAt: string): void {
  const workflow = workflows.get(caseId)
  if (!workflow) return
  workflows.set(caseId, { ...workflow, committedAt })
  notify()
}

export function clearAdjudicationWorkflow(caseId: string): void {
  if (workflows.delete(caseId)) notify()
}

export function clearAllAdjudicationWorkflows(): void {
  if (workflows.size === 0) return
  workflows.clear()
  notify()
}

export function isCommitEligible(
  outcome: AdjudicationOutcome | undefined,
  committedAt: string | null | undefined
): boolean {
  return outcome === 'ACCEPTED' && !committedAt
}

export function subscribeAdjudicationWorkflows(listener: Listener): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function useAdjudicationWorkflow(caseId: string): AdjudicationWorkflow | null {
  return useSyncExternalStore(
    subscribeAdjudicationWorkflows,
    () => getAdjudicationWorkflow(caseId),
    () => null
  )
}
