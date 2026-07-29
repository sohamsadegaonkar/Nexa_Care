import { ApiError } from '../../utils/apiClient'

const TERMINAL_ADJUDICATION_ACCESS_CODES = new Set([
  'ADJUDICATION_SESSION_MISMATCH',
  'ADJUDICATION_ACCESS_DENIED',
  'ADJUDICATION_CONSENT_INACTIVE',
  'ADJUDICATION_ERASURE_ACCESS_BLOCKED',
  'ADJUDICATION_ERASURE_REGISTRY_UNAVAILABLE',
  'FORBIDDEN',
])

export function isTerminalAdjudicationAccessError(reason: unknown): boolean {
  return (
    reason instanceof ApiError &&
    (reason.status === 403 || TERMINAL_ADJUDICATION_ACCESS_CODES.has(reason.code ?? ''))
  )
}
