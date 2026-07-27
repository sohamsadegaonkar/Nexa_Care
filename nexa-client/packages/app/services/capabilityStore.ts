/**
 * In-memory workflow capability store (DEFECT 3).
 *
 * The raw consent bearer token may exist only in process memory and in the
 * `X-Consent-Token` header of outgoing requests. It must never be written
 * to a URL (path, query string, or fragment), router state, localStorage,
 * sessionStorage, IndexedDB, AsyncStorage, SecureStore, logs, analytics, or
 * error-reporting metadata.
 *
 * Screens navigate using an opaque `workflow_id` (and `patient_id` /
 * `job_id` / `review_item_id` where relevant) instead of the token itself.
 * The receiving screen looks the token up here by `workflow_id`. On a page
 * refresh or process restart this module's memory is gone by construction
 * -- that is the point, not a bug -- so the receiving screen must show
 * "Access session expired -- request access again." rather than trying to
 * recover the token from anywhere durable.
 */

import { useSyncExternalStore } from 'react'

export type CapabilityGrant = {
  workflowId: string
  patientId: string
  token: string
  purpose: string
  scope: string[]
  expiresAt: string
  jobId?: string
}

type Listener = () => void

const grants = new Map<string, CapabilityGrant>()
const listeners = new Set<Listener>()

function notify(): void {
  for (const listener of listeners) listener()
}

function isExpired(grant: CapabilityGrant): boolean {
  const expiresAtMs = Date.parse(grant.expiresAt)
  return Number.isFinite(expiresAtMs) && expiresAtMs <= Date.now()
}

/** Generate an opaque, non-secret workflow correlation id (not the token). */
export function generateWorkflowId(): string {
  const cryptoObj = (globalThis as { crypto?: { randomUUID?: () => string } }).crypto
  if (cryptoObj?.randomUUID) return cryptoObj.randomUUID()
  // Fallback for environments without crypto.randomUUID -- fine here since
  // this id is a correlation key, never the secret itself.
  return `wf_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 12)}`
}

/** Store a freshly issued capability, keyed by its workflow id. */
export function setCapability(grant: CapabilityGrant): void {
  grants.set(grant.workflowId, grant)
  notify()
}

/** Look up a live capability by workflow id. Returns null if missing/expired. */
export function getCapability(workflowId: string | null | undefined): CapabilityGrant | null {
  if (!workflowId) return null
  const grant = grants.get(workflowId)
  if (!grant) return null
  if (isExpired(grant)) {
    grants.delete(workflowId)
    notify()
    return null
  }
  return grant
}

/** Update the jobId on an existing capability (pipeline flows attach a job after issue). */
export function attachJobId(workflowId: string, jobId: string): void {
  const grant = grants.get(workflowId)
  if (!grant) return
  grants.set(workflowId, { ...grant, jobId })
  notify()
}

export function clearCapability(workflowId: string): void {
  if (grants.delete(workflowId)) notify()
}

/** Clear everything: call on logout, auth failure, app reset. */
export function clearAllCapabilities(): void {
  if (grants.size === 0) return
  grants.clear()
  notify()
}

export function subscribe(listener: Listener): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

/**
 * React hook: re-renders when the capability for `workflowId` changes
 * (issued, attached job id, cleared, or naturally expires on next read).
 */
export function useCapability(workflowId: string | null | undefined): CapabilityGrant | null {
  return useSyncExternalStore(
    subscribe,
    () => getCapability(workflowId),
    () => null
  )
}
