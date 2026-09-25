import { RuntimeConfigError, resolveConfiguredApiUrl } from './runtimeConfig'
import {
  ProviderWebAuthenticatedStateSchema,
  ProviderWebLoginStateSchema,
  ProviderWebSessionSchema,
  validateOrThrow,
} from '../schemas/authNfcSchemas'

/**
 * Canonical Shared API Client for Nexa Care Alpha Demo
 * All frontend features MUST import and use this client.
 * Direct fetch() or axios calls in feature modules are strictly prohibited.
 */

export type AuthTokenProvider = () => Promise<string | null | undefined> | string | null | undefined
let authTokenProvider: AuthTokenProvider = () => null
let providerCookieAuthEnabled = false
export function setAuthTokenProvider(provider: AuthTokenProvider): void {
  authTokenProvider = provider
}
export function setProviderCookieAuthEnabled(enabled: boolean): void {
  providerCookieAuthEnabled = enabled
}
export async function getAuthToken(): Promise<string | null> {
  const token = await authTokenProvider()
  return typeof token === 'string' && token.trim() ? token.trim() : null
}

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
  protocol_version: 'nexa-consent-v3'
  discovery_handle: string
  purpose:
    | 'treatment'
    | 'emergency_care'
    | 'diagnostic_review'
    | 'follow_up'
    | 'referral'
    | 'document_processing'
  scope: 'clinical' | 'full' | 'documents'
  access_duration_seconds: number
  purpose_note?: string
}

export interface ConsentChallengeResponse {
  protocol_version: 'nexa-consent-v3'
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
  protocol_version: 'nexa-consent-v3'
  request_id: string
  patient_id: string
  provider_id: string
  hospital_id: string
  provider_name: string
  hospital_name: string
  purpose: string
  scope: string
  access_duration: number
  challenge_nonce: string
  issued_at: string
  expires_at: string
  consent_context_hash: string
  status: string
}

export interface SignedApprovalRequest {
  protocol_version: 'nexa-consent-v3'
  request_id: string
  patient_id: string
  decision: 'approved' | 'denied'
  challenge_nonce: string
  consent_context_hash: string
  signature: string
  device_id: string
  key_id: string
  key_version: number
  public_key_fingerprint: string
}

export interface SignedApprovalResponse {
  request_id: string
  status: 'approved' | 'denied'
  responded_at: string
}

export interface PatientConsentHistoryItem {
  id: string
  public_ref: string
  patient_id?: string
  purpose: string
  status: 'active' | 'expired' | 'revoked'
  scope: string[]
  issued_at: string
  expires_at: string
  revoked_at: string | null
  type: 'break-glass' | 'routine'
  is_treatment_session?: boolean
}

export type TreatmentSessionV1Operation = 'CREATE_ENCOUNTER' | 'WRITE_VITALS'

export interface TreatmentSessionV1Request {
  protocol_version: 'nexa-treatment-session-v1'
  discovery_handle: string
  purpose: string
  allowed_operations: TreatmentSessionV1Operation[]
  access_duration_seconds: number
}

export interface TreatmentSessionV1RequestResponse {
  protocol_version: 'nexa-treatment-session-v1'
  request_id: string
  status: 'pending'
  expires_in_seconds: number
  challenge_nonce: string
  treatment_context_hash: string
}

export interface TreatmentSessionV1Challenge {
  protocol_version: 'nexa-treatment-session-v1'
  request_id: string
  patient_id: string
  provider_id: string
  hospital_id: string
  provider_name: string
  hospital_name: string
  provider_session_binding_hash: string
  purpose: string
  allowed_operations: TreatmentSessionV1Operation[]
  access_duration: number
  challenge_nonce: string
  issued_at: string
  expires_at: string
  treatment_context_hash: string
  status: 'pending'
}

export interface SignedTreatmentSessionV1Request {
  protocol_version: 'nexa-treatment-session-v1'
  request_id: string
  patient_id: string
  decision: 'approved' | 'denied'
  challenge_nonce: string
  treatment_context_hash: string
  signature: string
  device_id: string
  key_id: string
  key_version: number
  public_key_fingerprint: string
}

export interface SignedTreatmentSessionV1Response {
  protocol_version: 'nexa-treatment-session-v1'
  request_id: string
  status: 'approved' | 'denied'
  responded_at: string
}

export interface TreatmentSessionV1ClaimResponse {
  protocol_version: 'nexa-treatment-session-v1'
  patient_id: string
  clinical_session_id: string
  treatment_token: string
  purpose: string
  allowed_operations: TreatmentSessionV1Operation[]
  expires_at: string
}

export interface TreatmentEncounterResponse {
  encounter_id: string
  clinical_session_id: string
}

export type TreatmentVitalRequest =
  | {
      kind: 'blood_pressure'
      systolic_bp: number
      diastolic_bp: number
      recorded_at: string
    }
  | {
      kind: 'heart_rate'
      beats_per_minute: number
      recorded_at: string
    }
  | {
      kind: 'temperature'
      celsius: number
      recorded_at: string
    }
  | {
      kind: 'spo2'
      percentage: number
      recorded_at: string
    }

export interface TreatmentVitalWriteResponse {
  status: 'committed'
  record_id: string
  encounter_id: string
  vital_type: 'BP' | 'HR' | 'temp' | 'SpO2'
  recorded_at: string
  idempotent_replay: boolean
}

export interface DashboardMetrics {
  total_patients: number
  active_consents: number
  break_glass_grants: number
  review_backlog: number
  definitions_version?: string
}

export interface ActiveTreatmentSessionItem {
  session_id: string
  encounter_id: string | null
  patient_id: string
  patient_display_identifier: string
  patient_name: string | null
  status: string
  purpose: string
  scope: string
  allowed_operations: string[]
  issued_at: string
  expires_at: string
}

export interface RecentEncounterItem {
  encounter_id: string
  clinical_session_id: string
  patient_id: string
  patient_display_identifier: string
  patient_name: string | null
  created_at: string
}

export interface WorkspaceSummaryCounts {
  active_sessions_count: number
  recent_encounters_count: number
  total_patients: number
}

export interface ProviderWorkspaceResponse {
  active_sessions: ActiveTreatmentSessionItem[]
  recent_encounters: RecentEncounterItem[]
  pending_access: any[]
  summary_counts: WorkspaceSummaryCounts
}

export interface ConsentStatusResponse {
  request_id: string
  patient_id?: string
  status: 'pending' | 'approved' | 'denied' | 'expired' | 'timeout' | 'cancelled'
  doctor_status?:
    | 'pending'
    | 'approved'
    | 'denied'
    | 'expired'
    | 'timeout'
    | 'cancelled'
    | 'delivery_failed'
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

/** Response shape from GET /api/v2/patient/{id}/emergency-summary (break-glass only). */
export interface EmergencySummaryCategoryItem {
  [key: string]: unknown
}
export interface EmergencySummaryCategory {
  category: string
  available: boolean
  items?: EmergencySummaryCategoryItem[]
  value?: unknown
  verified?: boolean
  verification_state?: string
  caveat?: string
}
export interface EmergencySummaryResponse {
  patient_id: string
  categories: Record<string, EmergencySummaryCategory>
  retrieved_at: string
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

export interface PatientHealthSummaryResponse {
  patient_id: string
  counts: {
    allergies: number
    medications: number
    vitals: number
    labs: number
    reports: number
  }
  allergy_highlights: Array<{
    allergen: string
    severity: string
    risk_level: string
    source: string
    confidence?: number | null
  }>
  active_medications: Array<{
    medication_name: string
    dosage: string
    frequency: string
    prescribed_at?: string | null
    source: string
    confidence?: number | null
  }>
  latest_vitals: Array<{
    type: string
    value: string
    unit: string
    recorded_at?: string | null
    source: string
  }>
  recent_labs: Array<{
    test_name: string
    value: string
    unit: string
    is_abnormal: boolean
    recorded_at?: string | null
    source: string
  }>
  recent_reports: Array<{
    report_id: string
    document_type: string
    uploaded_at?: string | null
    source: string
  }>
  recent_timeline_events: Array<{
    event_id: string
    event_type: string
    title: string
    summary: string
    occurred_at: string
    source: string
    source_display?: string
    confidence?: number | null
    risk_level?: string | null
    record_id?: string | null
    category?: string | null
  }>
  last_updated: string | null
}

export interface PatientRecordCategoryItem {
  category: 'allergies' | 'medications' | 'vitals' | 'labs' | 'documents'
  label: string
  icon: string
  count: number
  latest_record_date: string | null
  preview: string | null
}

export interface PatientRecordsCategoriesResponse {
  patient_id: string
  categories: PatientRecordCategoryItem[]
}

export interface PatientCategoryRecordsResponse {
  patient_id: string
  category: string
  records: Array<Record<string, any>>
  next_cursor: string | null
}

export interface PatientRecordDetailResponse {
  record_id: string
  patient_id: string
  category: string
  title: string
  fields: Record<string, any>
  recorded_at: string | null
  provenance: {
    source: string
    source_display: string
    hospital_name?: string | null
    encounter_recorded_at?: string | null
    confidence?: number | null
    risk_level?: string | null
    source_document_id?: string | null
    has_source_document: boolean
  }
}

export interface PatientPrescriptionItem {
  prescription_id: string
  medication_name: string
  strength: string
  frequency: string
  prescribed_at: string | null
  source: string
  source_display: string
  risk_level: string
  confidence?: number | null
  has_source_document: boolean
  source_document_id?: string | null
  is_external_document?: boolean
}

export interface PatientPrescriptionsResponse {
  patient_id: string
  prescriptions: PatientPrescriptionItem[]
  next_cursor: string | null
}

export interface PatientReportItem {
  report_id: string
  report_title: string
  document_type: string
  uploaded_at: string | null
  source: string
  source_display: string
  can_view_source: boolean
  category?: string
}

export interface PatientReportsResponse {
  patient_id: string
  reports: PatientReportItem[]
  next_cursor: string | null
}

export interface PatientDocumentDetailResponse {
  document_id: string
  patient_id: string
  document_type: string
  uploaded_at: string | null
  is_owner: boolean
  view_authorized: boolean
}

// ── Patient External Record Import Types (Slice 11E / D4) ──

export interface PatientExternalRecordActions {
  can_process: boolean
  can_retry: boolean
  can_cancel: boolean
  can_review: boolean
  can_save: boolean
  can_view_source: boolean
}

export interface PatientExternalRecordResponse {
  import_id: string
  category:
    | 'prescription'
    | 'lab_report'
    | 'imaging_report'
    | 'discharge_summary'
    | 'other_medical_record'
  status:
    | 'processing'
    | 'needs_review'
    | 'ready_to_save'
    | 'imported'
    | 'retry_available'
    | 'could_not_process'
    | 'cancelled'
  actions: PatientExternalRecordActions
  duplicate?: boolean
  source_available?: boolean
  created_at: string
}

export interface PatientExternalRecordUploadPolicy {
  max_upload_bytes: number
  accepted_extensions: string[]
  accepted_mime_types: string[]
}

export interface SelectedSourceFile {
  name: string
  type: string
  size: number
  file?: File | Blob
  uri?: string
}

export interface PatientExternalRecordReviewItem {
  review_item_id: string
  label: string
  extracted_value: string
  corrected_value: string | null
  decision: 'pending' | 'accepted' | 'corrected' | 'rejected'
  source_page: number | null
  source_text: string | null
  source_available: boolean
  confirmation_required: boolean
}

export interface PatientExternalRecordReviewResponse {
  import_id: string
  category:
    | 'prescription'
    | 'lab_report'
    | 'imaging_report'
    | 'discharge_summary'
    | 'other_medical_record'
  status: 'needs_review' | 'ready_to_save'
  items: PatientExternalRecordReviewItem[]
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
  status:
    | 'queued'
    | 'processing'
    | 'review_required'
    | 'review_pending'
    | 'auto_approved'
    | 'source_only'
    | 'quarantined'
    | 'failed'
    | 'extraction_pending'
    | 'extracting'
    | 'extraction_failed_retryable'
    | 'extraction_failed_terminal'
    | 'identity_mismatch'
    | 'validation_failed'
    | 'committed'
  document_type: string
  provider: string | null
  provider_version: string | null
  document_confidence: number | null
  routing_lane?: 'SOURCE_ONLY' | 'QUARANTINE' | null
  routing_reasons?: string[] | null
  candidate_count: number
  candidates: Array<{
    field_name: string
    raw_value: string
    field_confidence: number | null
    source_page: number | null
    source_text: string | null
    source_bbox: number[] | null
    evidence_complete: boolean
    lane: 'SOURCE_ONLY' | 'QUARANTINE'
    reason_codes: string[]
  }>
  identity_validation: 'passed' | 'failed' | 'not_completed'
  auto_commit_enabled: false
  clinician_adjudication_required: true
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

export type AdjudicationOutcome = 'ACCEPTED' | 'REJECTED' | 'NEEDS_SPECIALIST_REVIEW'
export type AdjudicationCaseStatus = 'PENDING' | 'ACCEPTED' | 'REJECTED' | 'NEEDS_SPECIALIST_REVIEW'

export type AdjudicationReasonCode =
  | 'SOURCE_VERIFIED'
  | 'MANUAL_TRANSCRIPTION'
  | 'CORRECTED_AGAINST_SOURCE'
  | 'NOT_CLINICAL_DATA'
  | 'ILLEGIBLE_SOURCE'
  | 'DUPLICATE_OBSERVATION'
  | 'SOURCE_MISMATCH'
  | 'SPECIALIST_INTERPRETATION_REQUIRED'
  | 'AMBIGUOUS_SOURCE'
  | 'OUT_OF_SUPPORTED_SCOPE'

export type AdjudicatedClinicalField =
  | {
      kind: 'VITAL'
      vital_type: 'HEART_RATE' | 'TEMPERATURE' | 'SPO2' | 'RESPIRATORY_RATE'
      reviewer_entered_value: number
      normalized_value: number
      unit: string
      effective_at: string
      page_number?: number | null
      provenance_type: 'HUMAN_TRANSCRIBED' | 'HUMAN_VERIFIED'
    }
  | {
      kind: 'LAB_RESULT'
      test_name: string
      reviewer_entered_value: number
      normalized_value: number
      unit: string
      reference_range: string
      is_abnormal: boolean
      effective_at: string
      page_number?: number | null
      provenance_type: 'HUMAN_TRANSCRIBED' | 'HUMAN_VERIFIED'
    }

export interface AdjudicationCaseResponse {
  case_id: string
  patient_id: string
  tenant_id: string
  source_document_id: string
  job_id: string
  routing_id: string | null
  decision_id: string | null
  reviewer_id: string
  reviewer_role: string
  status: AdjudicationCaseStatus
  version: number
  created_at: string
  resolved_at: string | null
  clinical_committed_at: string | null
}

export interface CreateAdjudicationCaseRequest {
  review_session_id: string
  idempotency_key: string
}

export interface AdjudicationSubmissionRequest {
  review_session_id: string
  idempotency_key: string
  outcome: AdjudicationOutcome
  fields: AdjudicatedClinicalField[]
  reason_codes: AdjudicationReasonCode[]
  supersedes_submission_id?: string
}

export interface AdjudicationSubmissionResponse {
  submission_id: string
  case_id: string
  outcome: AdjudicationOutcome
  attempt_number: number
  content_hash: string
  supersedes_submission_id: string | null
}

export interface AdjudicationCommitResponse {
  submission_id: string
  case_id: string
  status: 'committed'
  committed_at: string
  provenance: 'human_adjudicated'
}

export class ApiError extends Error {
  public status: number
  public code?: string
  public isRetryable: boolean
  public details?: unknown

  constructor(
    message: string,
    status: number,
    code?: string,
    isRetryable = false,
    details?: unknown
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.isRetryable = isRetryable
    this.details = details
  }
}

export interface ConsentAccessClaimResponse {
  patient_id: string
  consent_token: string
  purpose: string
  scope: 'clinical' | 'full' | 'documents'
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

export type ProviderLoginResponse = ProviderLoginSuccessResponse | ProviderMfaRequiredResponse

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
      const messages = value
        .map((item) => {
          if (typeof item === 'string') return item
          if (
            item &&
            typeof item === 'object' &&
            typeof (item as Record<string, unknown>).msg === 'string'
          ) {
            return (item as Record<string, unknown>).msg as string
          }
          return null
        })
        .filter(Boolean)
      if (messages.length) return messages.join('; ')
    }
  }
  if (Array.isArray(record.errors) && record.errors.length) {
    return backendMessage({ detail: record.errors }, fallback)
  }
  return fallback
}

function backendErrorCode(payload: unknown): string | null {
  if (!payload || typeof payload !== 'object') return null
  const record = payload as Record<string, unknown>
  if (typeof record.error_code === 'string') return record.error_code
  const detail = record.detail
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const code = (detail as Record<string, unknown>).error_code
    if (typeof code === 'string') return code
  }
  return null
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
  if (process.env.NODE_ENV === 'production') return

  const status = typeof fields.status === 'number' ? fields.status : null
  const handledClientFailure = status !== null && status >= 400 && status < 500
  const expectedAuthFailure =
    status === 401 ||
    status === 403 ||
    fields.code === 'AUTH_REQUIRED' ||
    fields.code === 'REAUTH_REQUIRED'
  const retryableTransportFailure =
    fields.code === 'REQUEST_TIMEOUT' ||
    fields.code === 'NETWORK_ERROR' ||
    fields.code === 'REQUEST_CANCELLED' ||
    (status !== null && status >= 500 && fields.retryable === true)

  const safePath = path.replace(
    /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi,
    ':id'
  )

  const message = JSON.stringify({
    method,
    path: safePath,
    status,
    code: fields.code ?? null,
    retryable: fields.retryable ?? false,
  })

  if (retryableTransportFailure) {
    console.warn(`API_REQUEST_RETRYABLE ${message}`)
    return
  }

  if (handledClientFailure || expectedAuthFailure) {
    console.warn(`API_REQUEST_REJECTED ${message}`)
    return
  }

  console.error(`API_REQUEST_ERROR ${message}`)
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
  forceCookieTransport = false,
  responseMode: 'json' | 'blob' = 'json'
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
  const browserCookieSession =
    typeof window !== 'undefined' && (providerCookieAuthEnabled || forceCookieTransport)
  if (!noAuth && !token && !browserCookieSession) {
    throw new ApiError(
      'Authentication is required before making this request.',
      0,
      'AUTH_REQUIRED',
      false
    )
  }
  const headers: Record<string, string> = {
    ...customHeaders,
  }

  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  if (
    browserCookieSession &&
    !['GET', 'HEAD', 'OPTIONS'].includes((options.method ?? 'GET').toUpperCase())
  ) {
    const csrf = document.cookie
      .split('; ')
      .find((item) => item.startsWith('nexa_csrf='))
      ?.split('=')[1]
    if (csrf) headers['X-CSRF-Token'] = decodeURIComponent(csrf)
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
      credentials: browserCookieSession ? 'include' : options.credentials,
      signal: controller.signal,
    })
  } catch (error: unknown) {
    const timedOut = controller.signal.aborted && !callerSignal?.aborted
    const code = timedOut
      ? 'REQUEST_TIMEOUT'
      : callerSignal?.aborted
        ? 'REQUEST_CANCELLED'
        : 'NETWORK_ERROR'
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
    let errorData: unknown = undefined

    try {
      const data = await response.json()
      errorData = data
      errorMsg = backendMessage(data, errorMsg)
      errorCode = backendErrorCode(data) ?? errorCode
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
      throw new ApiError(
        errorMsg || 'Authentication required or session expired',
        401,
        'REAUTH_REQUIRED',
        false,
        errorData
      )
    }

    if (response.status === 403) {
      throw new ApiError(errorMsg || 'Consent required or access denied', 403, errorCode, false, errorData)
    }

    if (response.status >= 500) {
      if (onErrorToast) onErrorToast(`Server error: ${errorMsg}`, true)
      throw new ApiError(errorMsg, response.status, errorCode, true, errorData)
    }

    throw new ApiError(errorMsg, response.status, errorCode, response.status === 429, errorData)
  }

  if (response.status === 204) {
    return {} as T
  }

  if (responseMode === 'blob') {
    return (await response.blob()) as T
  }

  try {
    return await response.json()
  } catch {
    diagnostic(options.method ?? 'GET', path, {
      status: response.status,
      code: 'MALFORMED_RESPONSE',
      retryable: true,
    })
    throw new ApiError(
      'The server returned an unreadable response.',
      response.status,
      'MALFORMED_RESPONSE',
      true
    )
  }
}

export const NexaApiClient = {
  // Device Enrollment
  enrollDevice(
    payload: DeviceEnrollmentRequest,
    consentToken: string
  ): Promise<DeviceEnrollmentResponse> {
    return request<DeviceEnrollmentResponse>(
      '/api/v2/patient/devices/enroll',
      {
        method: 'POST',
        body: JSON.stringify(payload),
      },
      {
        'X-Consent-Token': consentToken,
        'X-Consent-Purpose': 'device_enrollment',
      }
    )
  },

  listDevices(consentToken: string): Promise<EnrolledDevicesListResponse> {
    return request<EnrolledDevicesListResponse>(
      '/api/v2/patient/devices',
      {
        method: 'GET',
      },
      {
        'X-Consent-Token': consentToken,
        'X-Consent-Purpose': 'security_audit',
      }
    )
  },

  // Consent Handshake
  requestConsent(
    payload: ConsentChallengeRequest,
    hospitalId: string
  ): Promise<ConsentChallengeResponse> {
    return request<ConsentChallengeResponse>(
      '/api/v2/consent/v3/request',
      {
        method: 'POST',
        body: JSON.stringify(payload),
      },
      {
        'X-Hospital-Id': hospitalId,
      }
    )
  },

  discoverPatient(payload: { identifier_type: 'NEXA_PUBLIC_ID'; value: string }, hospitalId: string): Promise<{ discovery_handle: string; expires_at: string }> {
    return request('/api/v2/patient-discovery', { method: 'POST', body: JSON.stringify(payload) }, { 'X-Hospital-Id': hospitalId })
  },

  fetchConsentChallenge(requestId: string): Promise<FullConsentChallenge> {
    return request<FullConsentChallenge>(`/api/v2/consent/v3/challenge/${requestId}`, {
      method: 'GET',
    })
  },

  approveSignedConsent(payload: SignedApprovalRequest): Promise<SignedApprovalResponse> {
    return request<SignedApprovalResponse>('/api/v2/consent/v3/approve-signed', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  },

  denySignedConsent(payload: SignedApprovalRequest): Promise<SignedApprovalResponse> {
    if (payload.decision !== 'denied') throw new Error('denySignedConsent requires decision=denied')
    return request<SignedApprovalResponse>('/api/v2/consent/v3/approve-signed', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  },

  revokeDevice(
    deviceId: string
  ): Promise<{ device_id: string; status: string; revoked_at: string }> {
    return request(`/api/v2/patient/devices/${deviceId}/revoke`, { method: 'POST' })
  },

  providerLogin(payload: ProviderLoginRequest): Promise<ProviderLoginResponse> {
    return request<ProviderLoginResponse>(
      '/api/v2/auth/login',
      { method: 'POST', body: JSON.stringify(payload) },
      {},
      true
    )
  },

  providerMfaVerify(payload: ProviderMfaVerifyRequest): Promise<ProviderLoginSuccessResponse> {
    return request<ProviderLoginSuccessResponse>(
      '/api/v2/auth/mfa/verify',
      { method: 'POST', body: JSON.stringify(payload) },
      {},
      true
    )
  },

  providerWebLogin(
    payload: ProviderLoginRequest
  ): Promise<{ status: 'authenticated' | 'mfa_required'; expires_at?: string }> {
    return request(
      '/api/v2/auth/web/login',
      { method: 'POST', body: JSON.stringify(payload) },
      {},
      true,
      DEFAULT_TIMEOUT_MS,
      true
    ).then(
      (data) =>
        validateOrThrow(ProviderWebLoginStateSchema, data, 'provider web login') as {
          status: 'authenticated' | 'mfa_required'
          expires_at?: string
        }
    )
  },

  providerWebMfaVerify(totpCode: string): Promise<{ status: 'authenticated'; expires_at: string }> {
    return request(
      '/api/v2/auth/web/mfa/verify',
      {
        method: 'POST',
        body: JSON.stringify({ totp_code: totpCode }),
      },
      {},
      true,
      DEFAULT_TIMEOUT_MS,
      true
    ).then(
      (data) =>
        validateOrThrow(ProviderWebAuthenticatedStateSchema, data, 'provider web MFA') as {
          status: 'authenticated'
          expires_at: string
        }
    )
  },

  providerWebSession(): Promise<{
    authenticated: boolean
    expires_at: string
    provider_uid: string
    hospital_id: string
    display_name: string
    hospital_name: string
    roles: string[]
  }> {
    return request(
      '/api/v2/auth/web/session',
      { method: 'GET' },
      {},
      false,
      DEFAULT_TIMEOUT_MS,
      true
    ).then(
      (data) =>
        validateOrThrow(ProviderWebSessionSchema, data, 'provider web session') as {
          authenticated: boolean
          expires_at: string
          provider_uid: string
          hospital_id: string
          display_name: string
          hospital_name: string
          roles: string[]
        }
    )
  },

  providerWebLogout(): Promise<void> {
    return request(
      '/api/v2/auth/web/logout',
      { method: 'POST' },
      {},
      false,
      DEFAULT_TIMEOUT_MS,
      true
    )
  },

  getConsentStatus(requestId: string, hospitalId: string): Promise<ConsentStatusResponse> {
    const normalizedHospitalId = hospitalId.trim()
    if (!normalizedHospitalId) {
      return Promise.reject(
        new ApiError(
          'Provider hospital context is unavailable. Sign in again.',
          0,
          'PROVIDER_CONTEXT_REQUIRED',
          false
        )
      )
    }
    return request<ConsentStatusResponse>(
      `/api/v2/consent/status/${requestId}`,
      {
        method: 'GET',
      },
      {
        'X-Hospital-Id': normalizedHospitalId,
      }
    )
  },

  claimConsentAccess(requestId: string, hospitalId: string): Promise<ConsentAccessClaimResponse> {
    return request<ConsentAccessClaimResponse>(
      `/api/v2/consent/v3/${encodeURIComponent(requestId)}/claim-access`,
      { method: 'POST' },
      { 'X-Hospital-Id': hospitalId }
    )
  },

  // Treatment Session V1 — operation-bound provider authority.
  createTreatmentSessionV1Request(
    payload: TreatmentSessionV1Request
  ): Promise<TreatmentSessionV1RequestResponse> {
    return request<TreatmentSessionV1RequestResponse>(
      '/api/v2/treatment-session/v1/request',
      { method: 'POST', body: JSON.stringify(payload) }
    )
  },

  fetchTreatmentSessionV1Challenge(requestId: string): Promise<TreatmentSessionV1Challenge> {
    return request<TreatmentSessionV1Challenge>(
      `/api/v2/treatment-session/v1/challenge/${encodeURIComponent(requestId)}`,
      { method: 'GET' }
    )
  },

  submitSignedTreatmentSessionV1(
    payload: SignedTreatmentSessionV1Request
  ): Promise<SignedTreatmentSessionV1Response> {
    return request<SignedTreatmentSessionV1Response>(
      '/api/v2/treatment-session/v1/approve-signed',
      { method: 'POST', body: JSON.stringify(payload) }
    )
  },

  claimTreatmentSessionV1(requestId: string): Promise<TreatmentSessionV1ClaimResponse> {
    return request<TreatmentSessionV1ClaimResponse>(
      `/api/v2/treatment-session/v1/${encodeURIComponent(requestId)}/claim`,
      { method: 'POST' }
    )
  },

  createTreatmentSessionEncounter(treatmentToken: string): Promise<TreatmentEncounterResponse> {
    return request<TreatmentEncounterResponse>(
      '/api/v2/treatment-session/v1/encounter',
      { method: 'POST' },
      { 'X-Treatment-Token': treatmentToken }
    )
  },

  writeTreatmentSessionVital(
    treatmentToken: string,
    idempotencyKey: string,
    payload: TreatmentVitalRequest
  ): Promise<TreatmentVitalWriteResponse> {
    return request<TreatmentVitalWriteResponse>(
      '/api/v2/treatment-session/v1/vitals',
      { method: 'POST', body: JSON.stringify(payload) },
      {
        'X-Treatment-Token': treatmentToken,
        'Idempotency-Key': idempotencyKey,
      }
    )
  },

  // Patient Records
  getPatientSummary(
    patientId: string,
    consentToken: string,
    hospitalId: string,
    purpose = 'clinical_summary'
  ): Promise<PatientSummaryResponse> {
    return request<PatientSummaryResponse>(
      `/api/v2/patient/${patientId}/summary`,
      {
        method: 'GET',
      },
      {
        'X-Consent-Token': consentToken,
        'X-Consent-Purpose': purpose,
        'X-Hospital-Id': hospitalId,
      }
    )
  },

  getPatientTimeline(
    patientId: string,
    consentToken: string,
    hospitalId: string,
    limit = 20,
    cursor?: string
  ): Promise<PatientTimelineResponse> {
    const params = new URLSearchParams({ limit: String(limit) })
    if (cursor) params.append('cursor', cursor)
    return request<PatientTimelineResponse>(
      `/api/v2/patient/${patientId}/timeline?${params.toString()}`,
      {
        method: 'GET',
      },
      {
        'X-Consent-Token': consentToken,
        'X-Consent-Purpose': 'timeline_view',
        'X-Hospital-Id': hospitalId,
      }
    )
  },

  getPatientRecord(
    patientId: string,
    consentToken: string,
    purpose = 'clinical_view'
  ): Promise<any> {
    return request<any>(
      `/api/v2/patient/${patientId}/record`,
      {
        method: 'GET',
      },
      {
        'X-Consent-Token': consentToken,
        'X-Consent-Purpose': purpose,
      }
    )
  },

  /** Break-glass capabilities must use this endpoint, never getPatientRecord
   * (the general record endpoint rejects break-glass tokens outright). */
  getEmergencySummary(patientId: string, consentToken: string): Promise<EmergencySummaryResponse> {
    return request<EmergencySummaryResponse>(
      `/api/v2/patient/${patientId}/emergency-summary`,
      {
        method: 'GET',
      },
      {
        'X-Consent-Token': consentToken,
      }
    )
  },

  appendVitals(
    patientId: string,
    payload: AppendVitalsRequest,
    consentToken: string
  ): Promise<AppendRecordResponse> {
    return request<AppendRecordResponse>(
      `/api/v2/patient/${patientId}/record/vitals`,
      {
        method: 'POST',
        body: JSON.stringify(payload),
      },
      {
        'X-Consent-Token': consentToken,
        'X-Consent-Purpose': 'clinical_append',
      }
    )
  },

  // AI Pipeline & Review Queue
  uploadDocument(
    patientId: string,
    formData: FormData,
    consentToken: string
  ): Promise<DocumentUploadResponse> {
    return request<DocumentUploadResponse>(
      '/api/v2/pipeline/documents/upload',
      {
        method: 'POST',
        body: formData,
      },
      {
        'X-Consent-Token': consentToken,
        'X-Consent-Purpose': 'ai_document_ingestion',
      }
    )
  },

  async getExtractionJobStatus(
    jobId: string,
    consentToken: string
  ): Promise<ExtractionJobStatusResponse> {
    const response = await request<ExtractionJobStatusResponse>(
      `/api/v2/pipeline/jobs/${jobId}`,
      {
        method: 'GET',
      },
      {
        'X-Consent-Token': consentToken,
        'X-Consent-Purpose': 'pipeline_status',
      }
    )
    const routingReasons =
      Array.isArray(response.routing_reasons) &&
      response.routing_reasons.every((reason) => typeof reason === 'string')
        ? response.routing_reasons
        : []

    return {
      ...response,
      extracted_fields: Array.isArray(response.extracted_fields) ? response.extracted_fields : [],
      routing_reasons: routingReasons,
    }
  },

  getReviewQueue(
    hospitalId: string,
    consentToken: string,
    status = 'needs_review'
  ): Promise<ReviewQueueListResponse> {
    const params = new URLSearchParams({ hospital_id: hospitalId, status })
    return request<ReviewQueueListResponse>(
      `/api/v2/pipeline/review-queue?${params.toString()}`,
      {
        method: 'GET',
      },
      {
        'X-Consent-Token': consentToken,
        'X-Consent-Purpose': 'clinical_review',
      }
    )
  },

  reviewField(
    fieldId: string,
    payload: FieldReviewRequest,
    consentToken: string
  ): Promise<FieldReviewResponse> {
    return request<FieldReviewResponse>(
      `/api/v2/pipeline/fields/${fieldId}/review`,
      {
        method: 'POST',
        body: JSON.stringify(payload),
      },
      {
        'X-Consent-Token': consentToken,
        'X-Consent-Purpose': 'field_adjudication',
      }
    )
  },

  commitExtractionJob(
    jobId: string,
    payload: CommitJobRequest,
    consentToken: string
  ): Promise<CommitJobResponse> {
    return request<CommitJobResponse>(
      `/api/v2/pipeline/jobs/${jobId}/commit`,
      {
        method: 'POST',
        body: JSON.stringify(payload),
      },
      {
        'X-Consent-Token': consentToken,
        'X-Consent-Purpose': 'pipeline_commit',
      }
    )
  },

  createAdjudicationCaseFromRoute(
    routingId: string,
    payload: CreateAdjudicationCaseRequest
  ): Promise<AdjudicationCaseResponse> {
    return request<AdjudicationCaseResponse>(
      `/api/v2/pipeline/routing/${encodeURIComponent(routingId)}/adjudication-cases`,
      { method: 'POST', body: JSON.stringify(payload) }
    )
  },

  createDocumentAdjudicationCase(
    jobId: string,
    payload: CreateAdjudicationCaseRequest
  ): Promise<AdjudicationCaseResponse> {
    return request<AdjudicationCaseResponse>(
      `/api/v2/pipeline/jobs/${encodeURIComponent(jobId)}/document-adjudication-cases`,
      { method: 'POST', body: JSON.stringify(payload) }
    )
  },

  listAdjudicationCases(): Promise<AdjudicationCaseResponse[]> {
    return request<AdjudicationCaseResponse[]>('/api/v2/pipeline/adjudication-cases', {
      method: 'GET',
    })
  },

  getAdjudicationCase(caseId: string): Promise<AdjudicationCaseResponse> {
    return request<AdjudicationCaseResponse>(
      `/api/v2/pipeline/adjudication-cases/${encodeURIComponent(caseId)}`,
      { method: 'GET' }
    )
  },

  recoverAdjudicationSession(
    caseId: string,
    reviewSessionId: string
  ): Promise<AdjudicationCaseResponse> {
    return request<AdjudicationCaseResponse>(
      `/api/v2/pipeline/adjudication-cases/${encodeURIComponent(caseId)}/recover-session`,
      {
        method: 'POST',
        body: JSON.stringify({ review_session_id: reviewSessionId }),
      }
    )
  },

  getAdjudicationSource(caseId: string, reviewSessionId: string): Promise<Blob> {
    return request<Blob>(
      `/api/v2/pipeline/adjudication-cases/${encodeURIComponent(caseId)}/source`,
      { method: 'GET' },
      { 'X-Review-Session-ID': reviewSessionId },
      false,
      DEFAULT_TIMEOUT_MS,
      false,
      'blob'
    )
  },

  submitAdjudication(
    caseId: string,
    payload: AdjudicationSubmissionRequest
  ): Promise<AdjudicationSubmissionResponse> {
    return request<AdjudicationSubmissionResponse>(
      `/api/v2/pipeline/adjudication-cases/${encodeURIComponent(caseId)}/submissions`,
      { method: 'POST', body: JSON.stringify(payload) }
    )
  },

  commitAdjudicationSubmission(
    submissionId: string,
    reviewSessionId: string
  ): Promise<AdjudicationCommitResponse> {
    return request<AdjudicationCommitResponse>(
      `/api/v2/pipeline/adjudication-submissions/${encodeURIComponent(submissionId)}/commit`,
      { method: 'POST' },
      { 'X-Review-Session-ID': reviewSessionId }
    )
  },

  getConsentHistory(): Promise<any> {
    return request<any>('/api/v2/consent/history', { method: 'GET' })
  },

  getAccessLog(patientUuid: string): Promise<any> {
    return request<any>(`/api/v2/patient/${patientUuid}/access-log`, { method: 'GET' })
  },

  login(payload: any): Promise<any> {
    const login_identifier = payload.login_identifier || payload.email || ''
    return this.providerLogin({
      login_identifier,
      password: payload.password,
      hospital_id: payload.hospital_id,
    })
  },

  verifyMfa(payload: any): Promise<any> {
    const totp_code = payload.totp_code || payload.code || ''
    return this.providerMfaVerify({
      mfa_token: payload.mfa_token,
      totp_code,
      provider_id: payload.provider_id,
      hospital_id: payload.hospital_id,
    })
  },

  getDashboardMetrics(): Promise<DashboardMetrics> {
    return request<DashboardMetrics>('/api/v2/dashboard/metrics', { method: 'GET' })
  },

  getProviderWorkspace(): Promise<ProviderWorkspaceResponse> {
    return request<ProviderWorkspaceResponse>('/api/v2/provider/workspace', { method: 'GET' })
  },

  resolveNfcCard(payload: { card_uid: string }): Promise<any> {
    return request<any>('/api/v2/nfc/resolve', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  },

  /** Cancel a pending consent request (real server-side cancellation). */
  cancelConsentRequest(
    requestId: string
  ): Promise<{ request_id: string; status: string; cancelled_at: string }> {
    return request<{ request_id: string; status: string; cancelled_at: string }>(
      `/api/v2/consent/request/${requestId}/cancel`,
      { method: 'POST' }
    )
  },

  /** Revoke provider access previously approved by the authenticated patient. */
  revokeApprovedAccess(
    requestId: string
  ): Promise<{ request_id: string; status: 'revoked'; revoked_at: string }> {
    return request<{ request_id: string; status: 'revoked'; revoked_at: string }>(
      `/api/v2/consent/request/${encodeURIComponent(requestId)}/revoke`,
      { method: 'DELETE' }
    )
  },

  /** Fetch the authenticated patient's consent and treatment grant history. */
  getSelfConsentHistory(): Promise<PatientConsentHistoryItem[]> {
    return request<PatientConsentHistoryItem[]>('/api/v2/consent/history/self', {
      method: 'GET',
    })
  },

  /** Revoke one consent or treatment grant owned by the authenticated patient. */
  revokeSelfConsentGrant(
    publicRef: string
  ): Promise<{ public_ref: string; status: 'revoked'; revoked_at: string }> {
    return request<{ public_ref: string; status: 'revoked'; revoked_at: string }>(
      `/api/v2/consent/history/self/${encodeURIComponent(publicRef)}`,
      { method: 'DELETE' }
    )
  },

  /** Issue a break-glass emergency consent token (audited, rate-limited). */
  breakGlassIssue(payload: {
    patient_id: string
    reason_code: string
    justification: string
    requested_scope?: string[]
  }): Promise<{
    consent_token: string
    expires_at: string
    approved_scope: string[]
    policy_version: string
    authorization_ref: string
  }> {
    return request<{
      consent_token: string
      expires_at: string
      approved_scope: string[]
      policy_version: string
      authorization_ref: string
    }>('/api/v2/consent/break-glass/issue', { method: 'POST', body: JSON.stringify(payload) })
  },

  /** Issue emergency authority for the patient resolved by a one-use discovery capability. */
  breakGlassDiscoveredIssue(payload: {
    discovery_handle: string
    reason_code: string
    justification: string
    requested_scope?: string[]
    purpose?: 'EMERGENCY'
  }): Promise<{
    patient_id: string
    consent_token: string
    expires_at: string
    approved_scope: string[]
    policy_version: string
    authorization_ref: string
  }> {
    return request<{
      patient_id: string
      consent_token: string
      expires_at: string
      approved_scope: string[]
      policy_version: string
      authorization_ref: string
    }>('/api/v2/consent/break-glass/discovered/issue', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  },

  verifyActionMfa(code: string): Promise<{ verified: boolean }> {
    return request<{ verified: boolean }>('/api/v2/auth/mfa/verify-action', {
      method: 'POST',
      body: JSON.stringify({ code }),
    })
  },

  // Patient Longitudinal Health Record (Self-Access)
  getMyHealthSummary(): Promise<PatientHealthSummaryResponse> {
    return request<PatientHealthSummaryResponse>('/api/v2/patient/me/summary', { method: 'GET' })
  },

  getMyTimeline(options?: {
    cursor?: string | null
    category?: string | null
    limit?: number
  }): Promise<PatientTimelineResponse> {
    const params = new URLSearchParams()
    if (options?.limit) params.set('limit', String(options.limit))
    if (options?.cursor) params.set('cursor', options.cursor)
    if (options?.category) params.set('category', options.category)
    const qs = params.toString()
    return request<PatientTimelineResponse>(
      `/api/v2/patient/me/timeline${qs ? `?${qs}` : ''}`,
      { method: 'GET' }
    )
  },

  getMyRecordCategories(): Promise<PatientRecordsCategoriesResponse> {
    return request<PatientRecordsCategoriesResponse>('/api/v2/patient/me/records', { method: 'GET' })
  },

  getMyRecordsByCategory(
    category: string,
    options?: { cursor?: string | null; limit?: number }
  ): Promise<PatientCategoryRecordsResponse> {
    const params = new URLSearchParams()
    if (options?.limit) params.set('limit', String(options.limit))
    if (options?.cursor) params.set('cursor', options.cursor)
    const qs = params.toString()
    return request<PatientCategoryRecordsResponse>(
      `/api/v2/patient/me/records/${encodeURIComponent(category)}${qs ? `?${qs}` : ''}`,
      { method: 'GET' }
    )
  },

  getMyRecordDetail(
    category: string,
    recordId: string
  ): Promise<PatientRecordDetailResponse> {
    return request<PatientRecordDetailResponse>(
      `/api/v2/patient/me/records/${encodeURIComponent(category)}/${encodeURIComponent(recordId)}`,
      { method: 'GET' }
    )
  },

  getMyPrescriptions(options?: {
    cursor?: string | null
    limit?: number
    includeExternal?: boolean
  }): Promise<PatientPrescriptionsResponse> {
    const params = new URLSearchParams()
    if (options?.limit) params.set('limit', String(options.limit))
    if (options?.cursor) params.set('cursor', options.cursor)
    if (options?.includeExternal !== undefined)
      params.set('include_external', String(options.includeExternal))
    const qs = params.toString()
    return request<PatientPrescriptionsResponse>(
      `/api/v2/patient/me/prescriptions${qs ? `?${qs}` : ''}`,
      { method: 'GET' }
    )
  },

  getMyReports(options?: {
    cursor?: string | null
    limit?: number
    documentType?: string | null
  }): Promise<PatientReportsResponse> {
    const params = new URLSearchParams()
    if (options?.limit) params.set('limit', String(options.limit))
    if (options?.cursor) params.set('cursor', options.cursor)
    if (options?.documentType) params.set('document_type', options.documentType)
    const qs = params.toString()
    return request<PatientReportsResponse>(
      `/api/v2/patient/me/reports${qs ? `?${qs}` : ''}`,
      { method: 'GET' }
    )
  },

  getMyDocumentDetail(documentId: string): Promise<PatientDocumentDetailResponse> {
    return request<PatientDocumentDetailResponse>(
      `/api/v2/patient/me/documents/${encodeURIComponent(documentId)}`,
      { method: 'GET' }
    )
  },

  // ── Patient External Medical Record Import (Slice 11E / D4) ──

  getPatientUploadPolicy(): Promise<PatientExternalRecordUploadPolicy> {
    return request<PatientExternalRecordUploadPolicy>(
      '/api/v2/patient/me/external-records/upload-policy',
      { method: 'GET' }
    )
  },

  uploadPatientExternalRecord(
    category: string,
    fileSource: SelectedSourceFile | File | Blob,
    filename?: string,
    idempotencyKey?: string
  ): Promise<PatientExternalRecordResponse> {
    const formData = new FormData()
    formData.append('category', category)

    if (
      typeof fileSource === 'object' &&
      fileSource !== null &&
      'uri' in fileSource &&
      fileSource.uri
    ) {
      // Native React Native / Expo FormData contract
      formData.append('file', {
        uri: fileSource.uri,
        name: fileSource.name || filename || 'medical-record',
        type: fileSource.type || 'application/octet-stream',
      } as any)
    } else if (
      typeof fileSource === 'object' &&
      fileSource !== null &&
      'file' in fileSource &&
      fileSource.file
    ) {
      formData.append(
        'file',
        fileSource.file,
        fileSource.name || filename || 'medical-record'
      )
    } else if (typeof File !== 'undefined' && fileSource instanceof File) {
      formData.append('file', fileSource)
    } else {
      formData.append('file', fileSource as Blob, filename || 'medical-record')
    }

    const headers: Record<string, string> = {}
    if (idempotencyKey) {
      headers['Idempotency-Key'] = idempotencyKey
    }
    return request<PatientExternalRecordResponse>(
      '/api/v2/patient/me/external-records',
      {
        method: 'POST',
        body: formData,
      },
      headers
    )
  },

  getPatientExternalRecord(importId: string): Promise<PatientExternalRecordResponse> {
    return request<PatientExternalRecordResponse>(
      `/api/v2/patient/me/external-records/${encodeURIComponent(importId)}`,
      { method: 'GET' }
    )
  },

  processPatientExternalRecord(importId: string): Promise<PatientExternalRecordResponse> {
    return request<PatientExternalRecordResponse>(
      `/api/v2/patient/me/external-records/${encodeURIComponent(importId)}/process`,
      { method: 'POST' }
    )
  },

  retryPatientExternalRecord(importId: string): Promise<PatientExternalRecordResponse> {
    return request<PatientExternalRecordResponse>(
      `/api/v2/patient/me/external-records/${encodeURIComponent(importId)}/retry`,
      { method: 'POST' }
    )
  },

  cancelPatientExternalRecord(importId: string): Promise<PatientExternalRecordResponse> {
    return request<PatientExternalRecordResponse>(
      `/api/v2/patient/me/external-records/${encodeURIComponent(importId)}/cancel`,
      { method: 'POST' }
    )
  },

  getPatientExternalRecordReview(importId: string): Promise<PatientExternalRecordReviewResponse> {
    return request<PatientExternalRecordReviewResponse>(
      `/api/v2/patient/me/external-records/${encodeURIComponent(importId)}/review`,
      { method: 'GET' }
    )
  },

  reviewPatientExternalRecordItem(
    importId: string,
    reviewItemId: string,
    payload: {
      decision: 'accept' | 'correct' | 'reject'
      corrected_value?: string | null
    }
  ): Promise<PatientExternalRecordResponse> {
    return request<PatientExternalRecordResponse>(
      `/api/v2/patient/me/external-records/${encodeURIComponent(importId)}/review/${encodeURIComponent(reviewItemId)}`,
      {
        method: 'POST',
        body: JSON.stringify(payload),
      }
    )
  },

  savePatientExternalRecord(importId: string): Promise<PatientExternalRecordResponse> {
    return request<PatientExternalRecordResponse>(
      `/api/v2/patient/me/external-records/${encodeURIComponent(importId)}/save`,
      { method: 'POST' }
    )
  },

  getPatientExternalRecordSourceBlob(importId: string): Promise<Blob> {
    return request<Blob>(
      `/api/v2/patient/me/external-records/${encodeURIComponent(importId)}/source`,
      { method: 'GET' },
      {},
      false,
      DEFAULT_TIMEOUT_MS,
      false,
      'blob'
    )
  },
}

export interface ApiRequestConfig {
  headers?: Record<string, unknown>
  noAuth?: boolean
  timeoutMs?: number
  signal?: AbortSignal
}
export interface ApiResponse<T> {
  data: T
}
async function transport<T>(
  path: string,
  method: string,
  body?: unknown,
  config: ApiRequestConfig = {}
): Promise<ApiResponse<T>> {
  const data = await request<T>(
    path,
    {
      method,
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      ...(config.signal ? { signal: config.signal } : {}),
    },
    config.headers as Record<string, string> | undefined,
    config.noAuth,
    config.timeoutMs
  )
  return { data }
}
export const apiClient = {
  get: <T, R = ApiResponse<T>>(path: string, config?: ApiRequestConfig) =>
    transport<T>(path, 'GET', undefined, config) as Promise<R>,
  post: <T, R = ApiResponse<T>, D = unknown>(path: string, body?: D, config?: ApiRequestConfig) =>
    transport<T>(path, 'POST', body, config) as Promise<R>,
  put: <T, R = ApiResponse<T>, D = unknown>(path: string, body?: D, config?: ApiRequestConfig) =>
    transport<T>(path, 'PUT', body, config) as Promise<R>,
  patch<T>(path: string, body?: unknown, config?: ApiRequestConfig) {
    return transport<T>(path, 'PATCH', body, config)
  },
  delete<T>(path: string, config?: ApiRequestConfig) {
    return transport<T>(path, 'DELETE', undefined, config)
  },
}
