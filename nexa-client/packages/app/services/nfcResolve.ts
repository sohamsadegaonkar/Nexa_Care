/**
 * NFC card resolution service for the doctor web app.
 *
 * Resolves a physical NFC card UID to a patient identifier via
 * POST /api/v2/nfc/resolve. Handles merged-patient redirects
 * by surfacing canonical_patient_id and is_redirected.
 *
 * RUNTIME VALIDATION: The backend response is validated against a Zod schema
 * before the application trusts it. This catches contract drift between
 * frontend expectations and backend reality.
 *
 * ALPHA: Card UID is entered manually in the alpha demo.
 * Not yet: native NFC tap via expo-nfc or similar.
 *
 * Security notes:
 *   - Backend enforces rate limiting (30 scans/provider/minute).
 *   - Backend returns generic "not found" for 404 to prevent enumeration.
 *   - Backend audits every resolution attempt with partial card UID.
 *   - canonical_patient_id rewriting MUST be enforced server-side.
 *     The frontend banner is informational only — the backend must reject
 *     operations on old/merged patient IDs.
 *   - The doctor cannot manipulate canonical_patient_id in the URL to
 *     access a different patient because all data access requires a
 *     consent token validated server-side.
 */

import { NexaApiClient, ApiError } from '../utils/apiClient'
import {
  validateOrThrow,
  NfcResolveResponseSchema,
  SchemaValidationError,
} from '../schemas/authNfcSchemas'

// ── Types ────────────────────────────────────────────────────────────────────

export interface NfcResolveResponse {
  patient_id: string
  canonical_patient_id: string | null
  is_redirected: boolean
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

// ── Resolve ─────────────────────────────────────────────────────────────────

/**
 * Resolve an NFC card UID to the bound patient identifier.
 *
 * Backend: POST /api/v2/nfc/resolve
 * Request:  { card_uid: string }
 * Response: { patient_id, canonical_patient_id, is_redirected }
 *
 * When is_redirected is true, the patient record has been merged and
 * canonical_patient_id points to the surviving record. The caller
 * MUST use canonical_patient_id for all subsequent operations.
 *
 * The backend enforces:
 *   - Provider must be authenticated (401 if not)
 *   - Rate limiting: 30 scans/provider/minute (429 if exceeded)
 *   - Card must be active (403 if lost/revoked/inactive)
 *   - Audit logging of every resolution attempt
 *   - canonical_patient_id is a server-side invariant, not client-controlled
 */
export async function resolveNfcCard(cardUid: string): Promise<NfcResolveResponse> {
  const normalizedCardUid = cardUid.trim()

  if (!normalizedCardUid) {
    throw new NfcResolveError(
      'Card UID is required.',
      'NFC_RESOLVE_FAILED',
      false,
    )
  }

  try {
    const data = await NexaApiClient.resolveNfcCard({ card_uid: normalizedCardUid })

    // Validate backend response at runtime before trusting it
    const validated = validateOrThrow(
      NfcResolveResponseSchema,
      data,
      'NFC resolve response',
    )

    return {
      patient_id: validated.patient_id,
      canonical_patient_id: validated.canonical_patient_id ?? null,
      is_redirected: validated.is_redirected ?? false,
    }
  } catch (err: unknown) {
    // If Zod validation failed, wrap in a specific error code
    if (err instanceof SchemaValidationError) {
      throw new NfcResolveError(
        'Server returned an unexpected NFC resolution response. Please try again or contact support.',
        'NFC_SCHEMA_VALIDATION_FAILED',
        false,
      )
    }

    const status = (err as any)?.status
    if (status === 404) {
      throw new NfcResolveError(
        'NFC card not found.',
        'NFC_CARD_NOT_FOUND',
        false,
        status,
      )
    }
    if (status === 403) {
      throw new NfcResolveError(
        'Card is lost, revoked, or inactive.',
        'NFC_CARD_NOT_FOUND',
        false,
        status,
      )
    }
    if (status === 503) {
      throw new NfcResolveError(
        'NFC resolve service is temporarily unavailable.',
        'NFC_RESOLVE_UNAVAILABLE',
        true,
        status,
      )
    }
    if (status === 429) {
      throw new NfcResolveError(
        'Too many NFC scan attempts. Please wait.',
        'NFC_RESOLVE_FAILED',
        false,
        status,
      )
    }
    throw new NfcResolveError(
      'Unable to resolve NFC card.',
      'NFC_RESOLVE_FAILED',
      false,
    )
  }
}
