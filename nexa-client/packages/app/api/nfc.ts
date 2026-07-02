import axios, { type AxiosResponse } from 'axios'

import { apiClient } from '../utils/api'

export interface NfcResolveRequest {
  card_uid: string
}

export interface NfcResolveResponse {
  patient_id: string
}

export type NfcResolveErrorCode =
  | 'NFC_CARD_NOT_FOUND'
  | 'NFC_RESOLVE_UNAVAILABLE'
  | 'NFC_RESOLVE_FAILED'

export class NfcResolveError extends Error {
  public readonly code: NfcResolveErrorCode
  public readonly status?: number
  public readonly retryable: boolean

  public constructor(
    message: string,
    code: NfcResolveErrorCode,
    retryable: boolean,
    status?: number
  ) {
    super(message)
    this.name = 'NfcResolveError'
    this.code = code
    this.status = status
    this.retryable = retryable
  }
}

/**
 * Resolves an NFC card UID to the backend-authorized patient reference.
 */
export async function resolveNfcCard(cardUid: string): Promise<NfcResolveResponse> {
  const normalizedCardUid = cardUid.trim()

  if (!normalizedCardUid) {
    throw new NfcResolveError('Card UID is required.', 'NFC_RESOLVE_FAILED', false)
  }

  try {
    const response = await apiClient.post<
      NfcResolveResponse,
      AxiosResponse<NfcResolveResponse>,
      NfcResolveRequest
    >('/api/v2/nfc/resolve', { card_uid: normalizedCardUid })

    return response.data
  } catch (error: unknown) {
    if (axios.isAxiosError(error)) {
      const status = error.response?.status

      if (status === 404) {
        throw new NfcResolveError('NFC card was not found.', 'NFC_CARD_NOT_FOUND', false, status)
      }

      if (status === 503) {
        throw new NfcResolveError(
          'NFC resolve service is temporarily unavailable.',
          'NFC_RESOLVE_UNAVAILABLE',
          true,
          status
        )
      }
    }

    throw new NfcResolveError('Unable to resolve NFC card.', 'NFC_RESOLVE_FAILED', false)
  }
}
