/**
 * Runtime schema validation for backend API responses.
 *
 * TypeScript interfaces are compile-time only — they provide zero protection
 * at runtime. If the backend changes its response shape, the frontend will
 * silently trust malformed data. These Zod schemas validate every response
 * before the application trusts it.
 *
 * If a schema validation fails, the error is logged and the user sees
 * a clear message instead of silently corrupting application state.
 *
 * ALPHA: Schemas are derived from backend Pydantic models in
 * app/api/v2/auth_routes.py and app/api/v2/nfc_routes.py.
 * They MUST be kept in sync when the backend contract changes.
 */

import { z } from 'zod'

// ── Auth response schemas ────────────────────────────────────────────────────

/**
 * POST /api/v2/auth/login — direct success (no MFA required).
 *
 * Backend model: ProviderLoginResponse in auth_routes.py
 *   access_token: str
 *   token_type: str = "bearer"
 *   expires_at: datetime
 *   provider_uid: str
 *   hospital_id: UUID
 */
export const ProviderLoginSuccessSchema = z.object({
  access_token: z.string().min(1, 'access_token must be a non-empty string'),
  token_type: z.string().default('bearer'),
  expires_at: z.string().min(1, 'expires_at must be present'),
  provider_uid: z.string().min(1, 'provider_uid must be a non-empty string'),
  hospital_id: z.union([z.string().min(1), z.number()]).transform(String),
})

/**
 * POST /api/v2/auth/login — MFA required response.
 *
 * Backend model: ProviderLoginMfaRequiredResponse in auth_routes.py
 *   detail: str
 *   mfa_token: str
 */
export const ProviderLoginMfaRequiredSchema = z.object({
  detail: z.string().min(1, 'detail must be a non-empty string'),
  mfa_token: z.string().min(1, 'mfa_token must be a non-empty string'),
})

/**
 * POST /api/v2/auth/mfa/verify — MFA verification success.
 *
 * Same shape as ProviderLoginResponse.
 */
export const ProviderMfaVerifySuccessSchema = ProviderLoginSuccessSchema

// ── Consent request/response schemas ─────────────────────────────────────────

/**
 * POST /api/v2/consent/request — consent challenge creation response.
 *
 * Backend model: ConsentChallengeResponsePayload in consent_routes.py
 *   request_id: str
 *   status: str
 *   expires_in_seconds: int
 *   challenge_nonce: str | None
 */
export const ConsentChallengeResponseSchema = z.object({
  request_id: z.string().min(1, 'request_id must be a non-empty string'),
  status: z.string().min(1, 'status must be present'),
  expires_in_seconds: z.number().int().positive('expires_in_seconds must be positive'),
  challenge_nonce: z.string().nullable().default(null),
  notification_dispatch: z.enum(['queued', 'sent', 'failed', 'unavailable']).optional(),
  notification_queued: z.boolean().optional(),
  delivery_status: z.enum(['queued', 'sent', 'failed', 'unavailable', 'unknown']).optional(),
  delivery_error: z.string().nullable().optional(),
})

/**
 * GET /api/v2/consent/status/{request_id} — consent status poll response.
 *
 * Backend model: ConsentStatusResponsePayload in consent_routes.py
 *   request_id: str
 *   status: str
 *   responded_at: str | None
 */
export const ConsentStatusResponseSchema = z.object({
  request_id: z.string().min(1, 'request_id must be a non-empty string'),
  status: z.enum(['pending', 'approved', 'denied', 'expired', 'timeout', 'cancelled'], {
    errorMap: () => ({ message: 'status must be pending, approved, denied, expired, timeout, or cancelled' }),
  }),
  doctor_status: z.enum(['pending', 'approved', 'denied', 'expired', 'timeout', 'cancelled', 'delivery_failed']).optional(),
  delivery_status: z.enum(['queued', 'sent', 'failed', 'unavailable', 'unknown']).optional(),
  delivery_error: z.string().nullable().optional(),
  delivery_attempted_at: z.string().nullable().optional(),
  delivery_completed_at: z.string().nullable().optional(),
  responded_at: z.string().nullable().default(null),
})

/**
 * POST /api/v2/consent/request/{request_id}/cancel — cancel response.
 *
 * Backend model: ConsentCancelResponsePayload in consent_routes.py
 *   request_id: str
 *   status: str ("cancelled")
 *   cancelled_at: str
 */
export const ConsentCancelResponseSchema = z.object({
  request_id: z.string().min(1, 'request_id must be a non-empty string'),
  status: z.literal('cancelled', { errorMap: () => ({ message: 'cancel response status must be "cancelled"' }) }),
  cancelled_at: z.string().min(1, 'cancelled_at must be present'),
})

// ── NFC response schema ──────────────────────────────────────────────────────

/**
 * POST /api/v2/nfc/resolve — card resolution response.
 *
 * Backend model: NFCResolveResponse in nfc_routes.py
 *   patient_id: str
 *   canonical_patient_id: str | None = None
 *   is_redirected: bool = False
 */
export const NfcResolveResponseSchema = z.object({
  patient_id: z.string().min(1, 'patient_id must be a non-empty string'),
  canonical_patient_id: z.string().nullable().default(null),
  is_redirected: z.boolean().default(false),
})

// ── Discriminated union: login response ──────────────────────────────────────

/**
 * The login endpoint can return either a direct success or an MFA challenge.
 * This schema validates both cases and uses discriminated union detection:
 * if `mfa_token` is present → MFA required; otherwise → direct success.
 */
export const ProviderLoginResponseSchema = z.union([
  ProviderLoginMfaRequiredSchema,
  ProviderLoginSuccessSchema,
])

// ── Validation helpers ───────────────────────────────────────────────────────

export type ValidatedLoginSuccess = z.infer<typeof ProviderLoginSuccessSchema>
export type ValidatedLoginMfaRequired = z.infer<typeof ProviderLoginMfaRequiredSchema>
export type ValidatedMfaVerifySuccess = z.infer<typeof ProviderMfaVerifySuccessSchema>
export type ValidatedNfcResolveResponse = z.infer<typeof NfcResolveResponseSchema>
export type ValidatedConsentChallengeResponse = z.infer<typeof ConsentChallengeResponseSchema>
export type ValidatedConsentStatusResponse = z.infer<typeof ConsentStatusResponseSchema>
export type ValidatedConsentCancelResponse = z.infer<typeof ConsentCancelResponseSchema>

/**
 * Validate and parse a backend response. Returns the parsed data or throws
 * a SchemaValidationError with the Zod issues attached.
 */
export function validateOrThrow<T>(schema: z.ZodType<T>, data: unknown, label: string): T {
  const result = schema.safeParse(data)
  if (result.success) {
    return result.data
  }

  const issues = result.error.issues.map(
    (i) => `${i.path.join('.')}: ${i.message}`,
  )

  console.error(
    `[SchemaValidation] ${label} validation failed:`,
    issues,
  )

  throw new SchemaValidationError(
    `Backend response validation failed for ${label}. ` +
    `Issues: ${issues.join('; ')}`,
    issues,
  )
}

/**
 * Validate a login response, returning a discriminated result.
 * If neither schema matches, throws SchemaValidationError.
 */
export function validateLoginResponse(data: unknown):
  | { type: 'authenticated'; data: ValidatedLoginSuccess }
  | { type: 'mfa_required'; data: ValidatedLoginMfaRequired }
{
  // Try MFA-required first (has mfa_token field)
  const mfaResult = ProviderLoginMfaRequiredSchema.safeParse(data)
  if (mfaResult.success) {
    return { type: 'mfa_required', data: mfaResult.data }
  }

  // Try direct success
  const successResult = ProviderLoginSuccessSchema.safeParse(data)
  if (successResult.success) {
    return { type: 'authenticated', data: successResult.data }
  }

  // Neither matched — log both sets of issues
  console.error(
    '[SchemaValidation] Login response matched neither schema:',
    { mfaIssues: mfaResult.error.issues, successIssues: successResult.error.issues },
  )

  throw new SchemaValidationError(
    'Backend login response does not match any expected schema. ' +
    'This indicates a backend contract change that the frontend has not adapted to.',
    [
      ...mfaResult.error.issues.map((i) => `MFA schema — ${i.path.join('.')}: ${i.message}`),
      ...successResult.error.issues.map((i) => `Success schema — ${i.path.join('.')}: ${i.message}`),
    ],
  )
}

// ── Error class ──────────────────────────────────────────────────────────────

export class SchemaValidationError extends Error {
  public readonly issues: string[]

  constructor(message: string, issues: string[]) {
    super(message)
    this.name = 'SchemaValidationError'
    this.issues = issues
  }
}
