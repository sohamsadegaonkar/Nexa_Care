import { RuntimeConfigError, resolveConfiguredApiUrl } from './runtimeConfig'

/**
 * Canonical Shared API Client for Nexa Care Alpha Demo
 * All frontend features MUST import and use this client.
 * Direct fetch() or axios calls in feature modules are strictly prohibited.
 */

export type AuthTokenProvider = () => Promise<string | null | undefined> | string | null | undefined
let authTokenProvider: AuthTokenProvider = () => null
export function setAuthTokenProvider(provider: AuthTokenProvider): void { authTokenProvider = provider }
export async function getAuthToken(): Promise<string | null> { const token = await authTokenProvider(); return typeof token === 'string' && token.trim() ? token.trim() : null }

// Preserve the public export used by existing callers without allowing an
// invalid or missing value to become an implicit local endpoint.
export let API_BASE_URL = ''
try {
  API_BASE_URL = resolveConfiguredApiUrl()
} catch (error) {
  if (!(error instanceof RuntimeConfigError) || error.code !== 'MISSING_API_BASE_URL') throw error
}

// ── Types Matching docs/DATA-MODELS.md & docs/API-CONTRACTS.md ──

export interface ValidationResult {
  is_valid: boolean
  validation_errors: string[]
  reference_range?: {
    min: number
    max: number
    unit: string
  }
}

export interface ExtractedField {
  field_id: string
  job_id: string
  field_name: string
  raw_value: string
  normalized_value: string | null
  confidence: number
  risk_level: 'LOW_RISK' | 'MEDIUM_RISK' | 'HIGH_RISK' | 'CRITICAL_RISK'
  validation_result: ValidationResult
  source_page: number
  source_bbox: [number, number, number, number] | null
  status: 'auto_approved' | 'needs_review' | 'approved' | 'rejected' | 'edited'
  corrected_value: string | null
}

export interface DeviceEnrollmentRequest {
  patient_id: string
  device_name: string
  platform: 'ios' | 'android'
  expo_push_token: string
  public_key: string
}

export interface DeviceEnrollmentResponse {
  device_id: string
  patient_id: string
  status: 'active'
  enrolled_at: string
}

export interface EnrolledDevicesListResponse {
  patient_id: string
  devices: Array<{
    device_id: string
    device_name: string
    platform: 'ios' | 'android'
    public_key_fingerprint: string
    is_active: boolean
    last_used_at: string | null
    enrolled_at: string
  }>
}

export interface ConsentChallengeRequest {
  patient_id: string
  provider_id: string
  purpose: 'treatment' | 'emergency_care' | 'diagnostic_review' | 'follow_up' | 'referral'
  scope: 'clinical' | 'full'
  access_duration_seconds: number
  purpose_note?: string
}

export interface ConsentChallengeResponse {
  request_id: string
  challenge_nonce: string
  expires_in_seconds: number
  notification_dispatch?: 'queued' | 'sent' | 'failed' | 'unavailable'
  notification_queued?: boolean
  delivery_status?: 'queued' | 'sent' | 'failed' | 'unavailable' | 'unknown'
  delivery_error?: string | null
  status: 'pending'
}

export interface FullConsentChallenge {
  request_id: string
  patient_id: string
  provider_id: string
  provider_name: string
  hospital_name: string
  purpose: string
  scope: string
  access_duration: number
  challenge_nonce: string
  expires_at: string
  status: string
}

export interface SignedApprovalRequest {
  request_id: string
  patient_id: string
  decision: 'approved' | 'denied'
  challenge_nonce: string
  signature: string
  device_id: string
}

export interface SignedApprovalResponse {
  request_id: string
  status: 'approved' | 'denied'
  responded_at: string
}

export interface ConsentStatusResponse {
  request_id: string
  patient_id?: string
  status: 'pending' | 'approved' | 'denied' | 'expired' | 'timeout' | 'cancelled'
  doctor_status?: 'pending' | 'approved' | 'denied' | 'expired' | 'timeout' | 'cancelled' | 'delivery_failed'
  delivery_status?: 'queued' | 'sent' | 'failed' | 'unavailable' | 'unknown'
  delivery_error?: string | null
  delivery_attempted_at?: string | null
  delivery_completed_at?: string | null
  scope?: 'clinical' | 'full'
  resolved_at?: string
  responded_at?: string | null
}

export interface PatientSummaryResponse {
  patient_id: string
  pii: {
    patient_name: string
    phone: string
    aadhaar_abha_id: string
  }
  clinical_summary: {
    blood_group: string
    allergies: string[]
    chronic_conditions: string[]
    active_medications: Array<{
      name: string
      dosage: string
      frequency: string
    }>
  }
  shard_scope: 'clinical' | 'full'
}

export interface PatientTimelineResponse {
  patient_id: string
  events: Array<{
    event_id: string
    event_type: 'ENCOUNTER' | 'LAB_RESULT' | 'PRESCRIPTION' | 'DOCUMENT_INGESTED'
    title: string
    description: string
    event_date: string
    provider_name: string
    hospital_name: string
    data_payload: Record<string, any>
  }>
  next_cursor: string | null
}

export interface AppendVitalsRequest {
  encounter_id: string
  systolic_bp: number
  diastolic_bp: number
  heart_rate: number
  temperature_celsius: number
  sp_o2_percentage: number
  recorded_at: string
}

export interface AppendRecordResponse {
  record_id: string
  patient_id: string
  status: 'committed'
  audit_ledger_hash: string
}

export interface DocumentUploadResponse {
  job_id: string
  patient_id: string
  filename: string
  status: 'processing'
  estimated_completion_seconds: number
}

export interface ExtractionJobStatusResponse {
  job_id: string
  patient_id: string
  status: 'queued' | 'processing' | 'review_required' | 'auto_approved' | 'failed'
  document_type: 'PRESCRIPTION' | 'LAB_REPORT' | 'DISCHARGE_SUMMARY' | 'UNKNOWN'
  overall_confidence: number
  extracted_fields: ExtractedField[]
  created_at: string
}

export interface ReviewQueueListResponse {
  items: Array<{
    review_item_id: string
    job_id: string
    patient_id: string
    document_title: string
    flagged_fields_count: number
    highest_risk_level: 'LOW_RISK' | 'MEDIUM_RISK' | 'HIGH_RISK' | 'CRITICAL_RISK'
    queued_at: string
  }>
}

export interface FieldReviewRequest {
  action: 'approve' | 'reject' | 'edit'
  corrected_value?: string
  review_notes?: string
}

export interface FieldReviewResponse {
  field_id: string
  job_id: string
  previous_status: string
  new_status: 'approved' | 'rejected' | 'edited'
  final_value: string
  adjudicated_by: string
  adjudicated_at: string
}

export interface CommitJobRequest {
  patient_id: string
  encounter_summary?: string
}

export interface CommitJobResponse {
  job_id: string
  patient_id: string
  committed_fields_count: number
  timeline_event_id: string
  ledger_tx_hash: string
  committed_at: string
}

export class ApiError extends Error {
  public status: number
  public code?: string
  public isRetryable: boolean

  constructor(message: string, status: number, code?: string, isRetryable = false) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.isRetryable = isRetryable
  }
}

export interface ConsentAccessClaimResponse {
  patient_id: string
  consent_token: string
  purpose: string
  scope: 'clinical' | 'full'
  expires_at: string
}

export interface ProviderLoginRequest {
  login_identifier: string
  password: string
  hospital_id?: string
}

export interface ProviderLoginSuccessResponse {
  access_token: string
  token_type: string
  expires_at: string
  provider_uid: string
  hospital_id: string
}

export interface ProviderMfaRequiredResponse {
  detail: string
  mfa_token: string
}

export type ProviderLoginResponse =
  | ProviderLoginSuccessResponse
  | ProviderMfaRequiredResponse

export interface ProviderMfaVerifyRequest {
  mfa_token: string
  totp_code: string
  provider_id?: string
  hospital_id?: string
}

const DEFAULT_TIMEOUT_MS = 15_000

function backendMessage(payload: unknown, fallback: string): string {
  if (!payload || typeof payload !== 'object') return fallback
  const record = payload as Record<string, unknown>
  for (const key of ['detail', 'message', 'error']) {
    const value = record[key]
    if (typeof value === 'string' && value.trim()) return value.trim()
    if (Array.isArray(value) && value.length) {
      const messages = value.map((item) => {
        if (typeof item === 'string') return item
        if (item && typeof item === 'object' && typeof (item as Record<string, unknown>).msg === 'string') {
          return (item as Record<string, unknown>).msg as string
        }
        return null
      }).filter(Boolean)
      if (messages.length) return messages.join('; ')
    }
  }
  if (Array.isArray(record.errors) && record.errors.length) {
    return backendMessage({ detail: record.errors }, fallback)
  }
  return fallback
}

function statusCode(status: number): string {
  if (status === 400) return 'BAD_REQUEST'
  if (status === 401) return 'UNAUTHORIZED'
  if (status === 403) return 'FORBIDDEN'
  if (status === 404) return 'NOT_FOUND'
  if (status === 409) return 'CONFLICT'
  if (status === 422) return 'VALIDATION_ERROR'
  if (status === 429) return 'RATE_LIMITED'
  if (status >= 500) return 'SERVER_ERROR'
  return 'API_ERROR'
}

function diagnostic(method: string, path: string, fields: Record<string, unknown>): void {
  if (process.env.NODE_ENV !== 'production') {
    const status = typeof fields.status === 'number' ? fields.status : null
    const handledClientFailure = status !== null && status >= 400 && status < 500
    const expectedAuthFailure = status === 401
      || fields.code === 'AUTH_REQUIRED'
      || fields.code === 'REAUTH_REQUIRED'
    const log = handledClientFailure || expectedAuthFailure ? console.warn : console.error
    log('API_REQUEST_ERROR', { method, path, ...fields })
  }
}

export type ReAuthHandler = () => void
export type ErrorToastHandler = (message: string, retryable?: boolean) => void

let onReAuthRequired: ReAuthHandler | null = null
let onErrorToast: ErrorToastHandler | null = null

export function setApiErrorHandlers(reAuth: ReAuthHandler, toast: ErrorToastHandler): void {
  onReAuthRequired = reAuth
  onErrorToast = toast
}

async function request<T>(
  path: string,
  options: RequestInit = {},
  customHeaders: Record<string, string> = {},
  noAuth = false,
  timeoutMs = DEFAULT_TIMEOUT_MS,
): Promise<T> {
  if (!API_BASE_URL) {
    try {
      API_BASE_URL = resolveConfiguredApiUrl()
    } catch (error) {
      if (error instanceof RuntimeConfigError) {
        throw new ApiError(error.message, 0, error.code, false)
      }
      throw error
    }
  }

  const token = noAuth ? null : await getAuthToken()
  if (!noAuth && !token) {
    throw new ApiError('Authentication is required before making this request.', 0, 'AUTH_REQUIRED', false)
  }
  const headers: Record<string, string> = {
    ...customHeaders,
  }

  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  if (!(options.body instanceof FormData) && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json'
    headers['Accept'] = 'application/json'
  }

  const url = `${API_BASE_URL}${path}`
  let response: Response
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), timeoutMs)
  const callerSignal = options.signal
  const cancelFromCaller = () => controller.abort()
  callerSignal?.addEventListener('abort', cancelFromCaller, { once: true })

  try {
    response = await fetch(url, {
      ...options,
      headers,
      signal: controller.signal,
    })
  } catch (error: unknown) {
    const timedOut = controller.signal.aborted && !callerSignal?.aborted
    const code = timedOut ? 'REQUEST_TIMEOUT' : callerSignal?.aborted ? 'REQUEST_CANCELLED' : 'NETWORK_ERROR'
    const message = timedOut
      ? 'The request timed out. Please try again.'
      : callerSignal?.aborted
        ? 'The request was cancelled.'
        : 'Unable to reach Nexa Care. Check the configured server and network connection.'
    diagnostic(options.method ?? 'GET', path, {
      code,
      retryable: !callerSignal?.aborted,
      cause: error instanceof Error ? error.name : 'UnknownError',
    })
    if (onErrorToast) {
      onErrorToast(message, !callerSignal?.aborted)
    }
    throw new ApiError(message, 0, code, !callerSignal?.aborted)
  } finally {
    clearTimeout(timeout)
    callerSignal?.removeEventListener('abort', cancelFromCaller)
  }

  if (!response.ok) {
    let errorMsg = `HTTP Error ${response.status}`
    let errorCode = statusCode(response.status)

    try {
      const data = await response.json()
      errorMsg = backendMessage(data, errorMsg)
      if (typeof data?.error_code === 'string') errorCode = data.error_code
    } catch {
      // ignore JSON parse error on non-JSON response
    }

    diagnostic(options.method ?? 'GET', path, {
      status: response.status,
      code: errorCode,
      retryable: response.status === 429 || response.status >= 500,
    })

    if (response.status === 401 && !noAuth) {
      if (onReAuthRequired) onReAuthRequired()
      throw new ApiError(errorMsg || 'Authentication required or session expired', 401, 'REAUTH_REQUIRED', false)
    }

    if (response.status === 403) {
      throw new ApiError(
        errorMsg || 'Consent required or access denied',
        403,
        noAuth ? errorCode : 'CONSENT_REQUIRED',
        false,
      )
    }

    if (response.status >= 500) {
      if (onErrorToast) onErrorToast(`Server error: ${errorMsg}`, true)
      throw new ApiError(errorMsg, response.status, errorCode, true)
    }

    throw new ApiError(errorMsg, response.status, errorCode, response.status === 429)
  }

  if (response.status === 204) {
    return {} as T
  }

  try {
    return await response.json()
  } catch {
    diagnostic(options.method ?? 'GET', path, {
      status: response.status,
      code: 'MALFORMED_RESPONSE',
      retryable: true,
    })
    throw new ApiError('The server returned an unreadable response.', response.status, 'MALFORMED_RESPONSE', true)
  }
}

export const NexaApiClient = {
  // Device Enrollment
  enrollDevice(payload: DeviceEnrollmentRequest, consentToken: string): Promise<DeviceEnrollmentResponse> {
    return request<DeviceEnrollmentResponse>('/api/v2/patient/devices/enroll', {
      method: 'POST',
      body: JSON.stringify(payload),
    }, {
      'X-Consent-Token': consentToken,
      'X-Consent-Purpose': 'device_enrollment',
    })
  },

  listDevices(consentToken: string): Promise<EnrolledDevicesListResponse> {
    return request<EnrolledDevicesListResponse>('/api/v2/patient/devices', {
      method: 'GET',
    }, {
      'X-Consent-Token': consentToken,
      'X-Consent-Purpose': 'security_audit',
    })
  },

  // Consent Handshake
  requestConsent(payload: ConsentChallengeRequest, hospitalId: string): Promise<ConsentChallengeResponse> {
    return request<ConsentChallengeResponse>('/api/v2/consent/request', {
      method: 'POST',
      body: JSON.stringify(payload),
    }, {
      'X-Hospital-Id': hospitalId,
    })
  },

  fetchConsentChallenge(requestId: string): Promise<FullConsentChallenge> {
    return request<FullConsentChallenge>(`/api/v2/consent/challenge/${requestId}`, { method: 'GET' })
  },

  approveSignedConsent(payload: SignedApprovalRequest): Promise<SignedApprovalResponse> {
    return request<SignedApprovalResponse>('/api/v2/consent/approve-signed', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  },

  denySignedConsent(payload: SignedApprovalRequest): Promise<SignedApprovalResponse> {
    if (payload.decision !== 'denied') throw new Error('denySignedConsent requires decision=denied')
    return request<SignedApprovalResponse>('/api/v2/consent/approve-signed', { method: 'POST', body: JSON.stringify(payload) })
  },

  revokeDevice(deviceId: string): Promise<{ device_id: string; status: string; revoked_at: string }> {
    return request(`/api/v2/patient/devices/${deviceId}/revoke`, { method: 'POST' })
  },

  providerLogin(payload: ProviderLoginRequest): Promise<ProviderLoginResponse> {
    return request<ProviderLoginResponse>(
      '/api/v2/auth/login',
      { method: 'POST', body: JSON.stringify(payload) },
      {},
      true,
    )
  },

  providerMfaVerify(payload: ProviderMfaVerifyRequest): Promise<ProviderLoginSuccessResponse> {
    return request<ProviderLoginSuccessResponse>(
      '/api/v2/auth/mfa/verify',
      { method: 'POST', body: JSON.stringify(payload) },
      {},
      true,
    )
  },

  getConsentStatus(requestId: string, hospitalId: string): Promise<ConsentStatusResponse> {
    const normalizedHospitalId = hospitalId.trim()
    if (!normalizedHospitalId) {
      return Promise.reject(new ApiError(
        'Provider hospital context is unavailable. Sign in again.',
        0,
        'PROVIDER_CONTEXT_REQUIRED',
        false,
      ))
    }
    return request<ConsentStatusResponse>(`/api/v2/consent/status/${requestId}`, {
      method: 'GET',
    }, {
      'X-Hospital-Id': normalizedHospitalId,
    })
  },

  claimConsentAccess(requestId: string, hospitalId: string): Promise<ConsentAccessClaimResponse> {
    return request<ConsentAccessClaimResponse>(
      `/api/v2/consent/${encodeURIComponent(requestId)}/claim-access`,
      { method: 'POST' },
      { 'X-Hospital-Id': hospitalId },
    )
  },

  // Patient Records
  getPatientSummary(patientId: string, consentToken: string, hospitalId: string, purpose = 'clinical_summary'): Promise<PatientSummaryResponse> {
    return request<PatientSummaryResponse>(`/api/v2/patient/${patientId}/summary`, {
      method: 'GET',
    }, {
      'X-Consent-Token': consentToken,
      'X-Consent-Purpose': purpose,
      'X-Hospital-Id': hospitalId,
    })
  },

  getPatientTimeline(patientId: string, consentToken: string, hospitalId: string, limit = 20, cursor?: string): Promise<PatientTimelineResponse> {
    const params = new URLSearchParams({ limit: String(limit) })
    if (cursor) params.append('cursor', cursor)
    return request<PatientTimelineResponse>(`/api/v2/patient/${patientId}/timeline?${params.toString()}`, {
      method: 'GET',
    }, {
      'X-Consent-Token': consentToken,
      'X-Consent-Purpose': 'timeline_view',
      'X-Hospital-Id': hospitalId,
    })
  },

  getPatientRecord(patientId: string, consentToken: string, purpose = 'clinical_view'): Promise<any> {
    return request<any>(`/api/v2/patient/${patientId}/record`, {
      method: 'GET',
    }, {
      'X-Consent-Token': consentToken,
      'X-Consent-Purpose': purpose,
    })
  },

  appendVitals(patientId: string, payload: AppendVitalsRequest, consentToken: string): Promise<AppendRecordResponse> {
    return request<AppendRecordResponse>(`/api/v2/patient/${patientId}/record/vitals`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }, {
      'X-Consent-Token': consentToken,
      'X-Consent-Purpose': 'clinical_append',
    })
  },

  // AI Pipeline & Review Queue
  uploadDocument(patientId: string, formData: FormData, consentToken: string): Promise<DocumentUploadResponse> {
    return request<DocumentUploadResponse>('/api/v2/pipeline/documents/upload', {
      method: 'POST',
      body: formData,
    }, {
      'X-Consent-Token': consentToken,
      'X-Consent-Purpose': 'ai_document_ingestion',
    })
  },

  getExtractionJobStatus(jobId: string, consentToken: string): Promise<ExtractionJobStatusResponse> {
    return request<ExtractionJobStatusResponse>(`/api/v2/pipeline/jobs/${jobId}`, {
      method: 'GET',
    }, {
      'X-Consent-Token': consentToken,
      'X-Consent-Purpose': 'pipeline_status',
    })
  },

  getReviewQueue(hospitalId: string, consentToken: string, status = 'needs_review'): Promise<ReviewQueueListResponse> {
    const params = new URLSearchParams({ hospital_id: hospitalId, status })
    return request<ReviewQueueListResponse>(`/api/v2/pipeline/review-queue?${params.toString()}`, {
      method: 'GET',
    }, {
      'X-Consent-Token': consentToken,
      'X-Consent-Purpose': 'clinical_review',
    })
  },

  reviewField(fieldId: string, payload: FieldReviewRequest, consentToken: string): Promise<FieldReviewResponse> {
    return request<FieldReviewResponse>(`/api/v2/pipeline/fields/${fieldId}/review`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }, {
      'X-Consent-Token': consentToken,
      'X-Consent-Purpose': 'field_adjudication',
    })
  },

  commitExtractionJob(jobId: string, payload: CommitJobRequest, consentToken: string): Promise<CommitJobResponse> {
    return request<CommitJobResponse>(`/api/v2/pipeline/jobs/${jobId}/commit`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }, {
      'X-Consent-Token': consentToken,
      'X-Consent-Purpose': 'pipeline_commit',
    })
  },

  getConsentHistory(): Promise<any> {
    return request<any>('/api/v2/consent/history', { method: 'GET' })
  },

  getAccessLog(patientUuid: string): Promise<any> {
    return request<any>(`/api/v2/patient/${patientUuid}/access-log`, { method: 'GET' })
  },

  login(payload: any): Promise<any> {
    return request<any>('/api/v2/auth/login', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  },

  verifyMfa(payload: any): Promise<any> {
    return request<any>('/api/v2/auth/mfa/verify', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  },

  getDashboardMetrics(): Promise<any> {
    return request<any>('/api/v2/dashboard/metrics', { method: 'GET' })
  },

  resolveNfcCard(payload: { card_uid: string }): Promise<any> {
    return request<any>('/api/v2/nfc/resolve', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  },

  /** Cancel a pending consent request (real server-side cancellation). */
  cancelConsentRequest(requestId: string): Promise<{ request_id: string; status: string; cancelled_at: string }> {
    return request<{ request_id: string; status: string; cancelled_at: string }>(
      `/api/v2/consent/request/${requestId}/cancel`,
      { method: 'POST' },
    )
  },

  /** Issue a break-glass emergency consent token (audited, rate-limited). */
  breakGlassIssue(payload: { patient_id: string; reason_code: string; free_text: string }): Promise<{ consent_token: string; expires_at: string }> {
    return request<{ consent_token: string; expires_at: string }>(
      '/api/v2/consent/break-glass/issue',
      { method: 'POST', body: JSON.stringify(payload) },
    )
  },
}


export interface ApiRequestConfig { headers?: Record<string, unknown>; noAuth?: boolean; timeoutMs?: number; signal?: AbortSignal }
export interface ApiResponse<T> { data: T }
async function transport<T>(path: string, method: string, body?: unknown, config: ApiRequestConfig = {}): Promise<ApiResponse<T>> {
  const data = await request<T>(path, {
    method,
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    ...(config.signal ? { signal: config.signal } : {}),
  }, config.headers as Record<string, string> | undefined, config.noAuth, config.timeoutMs)
  return { data }
}
export const apiClient = {
  get: <T, R = ApiResponse<T>>(path: string, config?: ApiRequestConfig) => transport<T>(path, "GET", undefined, config) as Promise<R>,
  post: <T, R = ApiResponse<T>, D = unknown>(path: string, body?: D, config?: ApiRequestConfig) => transport<T>(path, "POST", body, config) as Promise<R>,
  put: <T, R = ApiResponse<T>, D = unknown>(path: string, body?: D, config?: ApiRequestConfig) => transport<T>(path, "PUT", body, config) as Promise<R>,
  patch: <T,>(path: string, body?: unknown, config?: ApiRequestConfig) => transport<T>(path, "PATCH", body, config),
  delete: <T,>(path: string, config?: ApiRequestConfig) => transport<T>(path, "DELETE", undefined, config),
}
