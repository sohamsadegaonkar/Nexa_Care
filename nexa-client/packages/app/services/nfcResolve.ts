/** Canonical Phase 1B.2 NFC client: returns only a discovery capability. */
import { NexaApiClient } from '../utils/apiClient'
import {
  validateOrThrow,
  NfcResolveResponseSchema,
  SchemaValidationError,
} from '../schemas/authNfcSchemas'

export interface NfcResolveResponse {
  discovery_handle: string
  expires_at: string
}
export type NfcResolveErrorCode =
  | 'NFC_CARD_NOT_FOUND'
  | 'NFC_RESOLVE_UNAVAILABLE'
  | 'NFC_RESOLVE_FAILED'
  | 'NFC_SCHEMA_VALIDATION_FAILED'
export class NfcResolveError extends Error {
  public readonly code: NfcResolveErrorCode
  public readonly retryable: boolean
  public readonly status?: number
  constructor(message: string, code: NfcResolveErrorCode, retryable: boolean, status?: number) {
    super(message)
    this.name = 'NfcResolveError'
    this.code = code
    this.retryable = retryable
    this.status = status
  }
}
export async function resolveNfcCard(cardUid: string): Promise<NfcResolveResponse> {
  const normalizedCardUid = cardUid.trim()
  if (!normalizedCardUid)
    throw new NfcResolveError('Card UID is required.', 'NFC_RESOLVE_FAILED', false)
  try {
    const data = await NexaApiClient.resolveNfcCard({ card_uid: normalizedCardUid })
    return validateOrThrow(NfcResolveResponseSchema, data, 'NFC resolve response')
  } catch (error: unknown) {
    if (error instanceof SchemaValidationError)
      throw new NfcResolveError(
        'Server returned an unexpected NFC resolution response.',
        'NFC_SCHEMA_VALIDATION_FAILED',
        false
      )
    const status = (error as { status?: number })?.status
    if (status === 403 || status === 404)
      throw new NfcResolveError(
        'NFC card could not be resolved.',
        'NFC_CARD_NOT_FOUND',
        false,
        status
      )
    if (status === 503)
      throw new NfcResolveError(
        'NFC resolve service is temporarily unavailable.',
        'NFC_RESOLVE_UNAVAILABLE',
        true,
        status
      )
    if (status === 429)
      throw new NfcResolveError(
        'Too many NFC scan attempts. Please wait.',
        'NFC_RESOLVE_FAILED',
        false,
        status
      )
    throw new NfcResolveError('Unable to resolve NFC card.', 'NFC_RESOLVE_FAILED', false)
  }
}
