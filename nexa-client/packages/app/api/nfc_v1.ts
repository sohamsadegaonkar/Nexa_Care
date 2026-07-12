
import { apiClient, ApiError, type ApiResponse } from '../utils/apiClient'

export interface NfcResolveRequest {
  card_uid: string
}

export interface NfcRedirectInfo {
  from: string
  to: string
  merged_at: string
}

export interface NfcResolveResponse {
  patient_id: string
  canonical_patient_id?: string
  is_redirected: boolean
  redirect_chain?: NfcRedirectInfo[]
  original_patient_uuid?: string
}

export type NfcResolveErrorCode =
  | 'NFC_CARD_NOT_FOUND'
  | 'NFC_RESOLVE_UNAVAILABLE'
  | 'NFC_RESOLVE_FAILED'

export class NfcResolveError extends Error {
  public readonly code: NfcResolveErrorCode
  public readonly status?: number

  constructor(
    message: string,
    code: NfcResolveErrorCode,
    status?: number
  ) {
    super(message)
    this.name = 'NfcResolveError'
    this.code = code
    this.status = status
  }
}

/**
 * Resolves NFC card with full tombstone redirect support (v1.0).
 */
export async function resolveNfcCardV1(cardUid: string): Promise<NfcResolveResponse> {
  const normalized = cardUid.trim()
  if (!normalized) {
    throw new NfcResolveError('Card UID is required', 'NFC_RESOLVE_FAILED')
  }

  try {
    const response = await apiClient.post<
      NfcResolveResponse,
      ApiResponse<NfcResolveResponse>,
      NfcResolveRequest
    >('/api/v2/nfc/resolve', { card_uid: normalized })

    return response.data
  } catch (error: unknown) {
    if (error instanceof ApiError) {
      const status = error.status
      if (status === 404) {
        throw new NfcResolveError('Card not found', 'NFC_CARD_NOT_FOUND', status)
      }
      if (status === 503) {
        throw new NfcResolveError('Service unavailable', 'NFC_RESOLVE_UNAVAILABLE', status)
      }
    }
    throw new NfcResolveError('Failed to resolve card', 'NFC_RESOLVE_FAILED')
  }
}
