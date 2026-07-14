/**
 * Canonical Shared API Client for Nexa Care Alpha Demo
 * All frontend features MUST import and use this client.
 * Direct fetch() or axios calls in feature modules are strictly prohibited.
 */

export type AuthTokenProvider = () => Promise<string | null | undefined> | string | null | undefined
let authTokenProvider: AuthTokenProvider = () => null
export function setAuthTokenProvider(provider: AuthTokenProvider): void { authTokenProvider = provider }
export async function getAuthToken(): Promise<string | null> { const token = await authTokenProvider(); return typeof token === 'string' && token.trim() ? token.trim() : null }

// Expo only exposes EXPO_PUBLIC_* variables to native bundles. Keep the Next
// variable as the web fallback so this shared client works on both platforms.
// Never hardcode localhost, LAN IPs, or deployment URLs here.
export const API_BASE_URL = (
  process.env.EXPO_PUBLIC_API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? ''
).replace(/\/$/, '')

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
  purpose: 'routine_checkup' | 'specialist_consult' | 'emergency' | 'ai_ingestion'
  scope: 'clinical' | 'full'
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
  consent_token?: string
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
): Promise<T> {
  if (!API_BASE_URL) {
    throw new ApiError(
      'API base URL is not configured. Set EXPO_PUBLIC_API_URL for Expo or NEXT_PUBLIC_API_URL for web.',
      0,
      'MISSING_API_BASE_URL',
      false,
    )
  }

  const token = noAuth ? null : await getAuthToken()
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

  try {
    response = await fetch(url, {
      ...options,
      headers,
    })
  } catch (err: any) {
    if (onErrorToast) {
      onErrorToast('Network connection failed. Please check your internet connection.', true)
    }
    throw new ApiError(err?.message || 'Network error', 0, 'NETWORK_ERROR', true)
  }

  if (!response.ok) {
    let errorMsg = `HTTP Error ${response.status}`
    let errorCode = 'API_ERROR'

    try {
      const data = await response.json()
      errorMsg = typeof data.detail === 'string' ? data.detail : data.message || errorMsg
      errorCode = data.error_code || errorCode
    } catch {
      // ignore JSON parse error on non-JSON response
    }

    if (response.status === 401) {
      if (onReAuthRequired) onReAuthRequired()
      throw new ApiError('Authentication required or session expired', 401, 'REAUTH_REQUIRED', false)
    }

    if (response.status === 403) {
      throw new ApiError(errorMsg || 'Consent required or access denied', 403, 'CONSENT_REQUIRED', false)
    }

    if (response.status >= 500) {
      if (onErrorToast) onErrorToast(`Server error: ${errorMsg}`, true)
      throw new ApiError(errorMsg, response.status, errorCode, true)
    }

    throw new ApiError(errorMsg, response.status, errorCode, false)
  }

  if (response.status === 204) {
    return {} as T
  }

  return response.json()
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

  providerLogin(payload: unknown): Promise<unknown> {
    return request('/api/v2/auth/login', { method: 'POST', body: JSON.stringify(payload) }, {}, true)
  },

  getConsentStatus(requestId: string, hospitalId: string): Promise<ConsentStatusResponse> {
    return request<ConsentStatusResponse>(`/api/v2/consent/status/${requestId}`, {
      method: 'GET',
    }, {
      'X-Hospital-Id': hospitalId,
    })
  },

  // Patient Records
  getPatientSummary(patientId: string, consentToken: string, purpose = 'clinical_summary'): Promise<PatientSummaryResponse> {
    return request<PatientSummaryResponse>(`/api/v2/patient/${patientId}/summary`, {
      method: 'GET',
    }, {
      'X-Consent-Token': consentToken,
      'X-Consent-Purpose': purpose,
    })
  },

  getPatientTimeline(patientId: string, consentToken: string, limit = 20, cursor?: string): Promise<PatientTimelineResponse> {
    const params = new URLSearchParams({ limit: String(limit) })
    if (cursor) params.append('cursor', cursor)
    return request<PatientTimelineResponse>(`/api/v2/patient/${patientId}/timeline?${params.toString()}`, {
      method: 'GET',
    }, {
      'X-Consent-Token': consentToken,
      'X-Consent-Purpose': 'timeline_view',
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


export interface ApiRequestConfig { headers?: Record<string, unknown>; noAuth?: boolean }
export interface ApiResponse<T> { data: T }
async function transport<T>(path: string, method: string, body?: unknown, config: ApiRequestConfig = {}): Promise<ApiResponse<T>> {
  const data = await request<T>(path, { method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) }, config.headers as Record<string, string> | undefined, config.noAuth)
  return { data }
}
export const apiClient = {
  get: <T, R = ApiResponse<T>>(path: string, config?: ApiRequestConfig) => transport<T>(path, "GET", undefined, config) as Promise<R>,
  post: <T, R = ApiResponse<T>, D = unknown>(path: string, body?: D, config?: ApiRequestConfig) => transport<T>(path, "POST", body, config) as Promise<R>,
  put: <T, R = ApiResponse<T>, D = unknown>(path: string, body?: D, config?: ApiRequestConfig) => transport<T>(path, "PUT", body, config) as Promise<R>,
  patch: <T,>(path: string, body?: unknown, config?: ApiRequestConfig) => transport<T>(path, "PATCH", body, config),
  delete: <T,>(path: string, config?: ApiRequestConfig) => transport<T>(path, "DELETE", undefined, config),
}
