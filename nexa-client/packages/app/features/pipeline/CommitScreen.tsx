/**
 * Commit screen — final review summary and commit to patient record.
 *
 * Displays a summary of the extraction job grouped by field status:
 * auto-approved, human-approved (clinician-verified), edited, and rejected.
 * Commits only fields explicitly approved or edited by a clinician.
 * Rejected fields are shown but will NOT be committed.
 *
 * Safety features:
 * - CommitSafetyBadge per field: auto-approved (green auto badge),
 *   human-approved (blue check), edited (yellow pencil), rejected (red X).
 * - HIGH/CRITICAL warning banner reminding reviewer to double-check.
 * - Commit button disabled until ALL fields are resolved.
 * - Clear count of unresolved fields blocking commit.
 * - Backend enforces that no `needs_review` fields remain (HTTP 409).
 *
 * ALPHA: This is an alpha implementation. The encounter summary field is
 * optional and may be required in future versions.
 *
 * SECURITY:
 * - All requests go through the shared NexaApiClient — no raw fetch/axios.
 * - Consent token passed as X-Consent-Token header.
 * - No hardcoded patient_id or provider_id.
 * - Session guard: must be authenticated.
 * - Backend enforces that no `needs_review` fields remain (HTTP 409).
 *
 * Route: /doctor/pipeline/commit/[jobId]?workflow_id=...&patient_id=...
 */

'use client'

import {
  YStack, H2, Paragraph, Button, Text, Spinner, Card, XStack, Separator, Input, ScrollView,
} from '@my/ui'
import { useState, useEffect, useCallback, useMemo } from 'react'
import { useRouter, useSearchParams, useParams } from 'next/navigation'
import {
  NexaApiClient,
  type ExtractionJobStatusResponse,
  type ExtractedField,
  type CommitJobResponse,
  ApiError,
} from '../../utils/apiClient'
import { useProviderAuth } from '../doctor/ProviderAuthContext'
import { useCapability } from '../../services/capabilityStore'

type CommitState = 'idle' | 'committing' | 'success' | 'error'

// ── CommitSafetyBadge — visual status indicator per field ───────────────

/**
 * CommitSafetyBadge — shows the provenance of a field in the commit summary.
 *
 * Four states:
 *   - auto_approved → blocking legacy-state badge
 *   - approved      → blue "Verified ✓" badge (clinician-verified)
 *   - edited        → yellow "Edited ✎" badge
 *   - rejected      → red "✕ Excluded" badge
 */
function CommitSafetyBadge({ status }: { status: string }) {
  if (status === 'auto_approved') {
    return (
      <Card backgroundColor="$red4" borderRadius="$4" paddingHorizontal="$2" paddingVertical="$1">
        <Text color="$red10" fontSize="$1" fontWeight="700">
          Legacy state — blocked
        </Text>
      </Card>
    )
  }
  if (status === 'approved') {
    return (
      <Card backgroundColor="$blue4" borderRadius="$4" paddingHorizontal="$2" paddingVertical="$1">
        <Text color="$blue10" fontSize="$1" fontWeight="700">
          Verified ✓
        </Text>
      </Card>
    )
  }
  if (status === 'edited') {
    return (
      <Card backgroundColor="$yellow4" borderRadius="$4" paddingHorizontal="$2" paddingVertical="$1">
        <Text color="$yellow10" fontSize="$1" fontWeight="700">
          Edited ✎
        </Text>
      </Card>
    )
  }
  if (status === 'rejected') {
    return (
      <Card backgroundColor="$red4" borderRadius="$4" paddingHorizontal="$2" paddingVertical="$1">
        <Text color="$red10" fontSize="$1" fontWeight="700">
          ✕ Excluded
        </Text>
      </Card>
    )
  }
  // needs_review — unresolved
  return (
    <Card backgroundColor="$orange4" borderRadius="$4" paddingHorizontal="$2" paddingVertical="$1">
      <Text color="$orange10" fontSize="$1" fontWeight="700">
        ⚠ Unresolved
      </Text>
    </Card>
  )
}

// ── CommitScreen component ─────────────────────────────────────────────

export function CommitScreen() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const routeParams = useParams()
  const { isAuthenticated } = useProviderAuth()

  // jobId comes from the [jobId] route param (camelCase); snake_case only in API payloads
  const jobId = (routeParams.jobId as string) ?? ''
  const patientId = searchParams.get('patient_id') ?? ''
  const workflowId = searchParams.get('workflow_id')
  const capability = useCapability(workflowId)
  const consentToken = capability?.token ?? ''

  const [job, setJob] = useState<ExtractionJobStatusResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [fetchError, setFetchError] = useState<string | null>(null)
  const [commitState, setCommitState] = useState<CommitState>('idle')
  const [commitError, setCommitError] = useState<string | null>(null)
  const [commitResult, setCommitResult] = useState<CommitJobResponse | null>(null)
  const [encounterSummary, setEncounterSummary] = useState('')

  // ── Session guard ────────────────────────────────────────────────────
  if (!isAuthenticated) {
    return (
      <YStack flex={1} backgroundColor="$background" justifyContent="center" alignItems="center" gap="$4" padding="$6">
        <Text color="$red10" fontSize="$6">🔒 Session Required</Text>
        <Paragraph color="$color10" fontSize="$3">
          Please log in to commit fields.
        </Paragraph>
        <Button theme="blue" onPress={() => router.push('/doctor/login')}>
          Go to Login
        </Button>
      </YStack>
    )
  }

  // ── Missing consent token guard ──────────────────────────────────────
  if (!consentToken) {
    return (
      <YStack flex={1} backgroundColor="$background" justifyContent="center" alignItems="center" gap="$4" padding="$6">
        <Text color="$red10" fontSize="$6">🔒 Consent Required</Text>
        <Paragraph color="$color10" fontSize="$3">
          {workflowId
            ? 'Access session expired — request access again.'
            : 'You must have an active consent grant to commit pipeline fields.'}
        </Paragraph>
        <Button theme="blue" onPress={() => router.push('/doctor/request-consent')}>
          Request Consent
        </Button>
      </YStack>
    )
  }

  // ── Fetch job ────────────────────────────────────────────────────────
  const fetchJob = useCallback(async () => {
    if (!jobId || !consentToken) return
    setLoading(true)
    setFetchError(null)
    try {
      const data = await NexaApiClient.getExtractionJobStatus(jobId, consentToken)
      setJob(data)
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 401) {
          router.push('/doctor/login')
          return
        }
        if (err.status === 403) {
          setFetchError('Consent required.')
          return
        }
        if (err.status === 404) {
          setFetchError('Job not found.')
          return
        }
      }
      setFetchError('Failed to load job details.')
    } finally {
      setLoading(false)
    }
  }, [jobId, consentToken, router])

  useEffect(() => {
    fetchJob()
  }, [fetchJob])

  // ── Computed: field categories ───────────────────────────────────────
  const fieldStats = useMemo(() => {
    if (!job) return {
      committable: 0,
      rejected: 0,
      needsReview: 0,
      autoApproved: [] as ExtractedField[],
      humanApproved: [] as ExtractedField[],
      edited: [] as ExtractedField[],
      rejectedFields: [] as ExtractedField[],
      unresolvedFields: [] as ExtractedField[],
      hasHighOrCriticalRisk: false,
    }
    const autoApproved = job.extracted_fields.filter(
      (f) => f.status === 'auto_approved',
    )
    const humanApproved = job.extracted_fields.filter(
      (f) => f.status === 'approved',
    )
    const edited = job.extracted_fields.filter(
      (f) => f.status === 'edited',
    )
    const rejectedFields = job.extracted_fields.filter(
      (f) => f.status === 'rejected',
    )
    const unresolvedFields = job.extracted_fields.filter(
      (f) => f.status === 'needs_review',
    )
    const committableFields = [...humanApproved, ...edited]
    const hasHighOrCriticalRisk = committableFields.some(
      (f) => f.risk_level === 'HIGH_RISK' || f.risk_level === 'CRITICAL_RISK',
    )
    return {
      committable: committableFields.length,
      rejected: rejectedFields.length,
      needsReview: unresolvedFields.length + autoApproved.length,
      autoApproved,
      humanApproved,
      edited,
      rejectedFields,
      unresolvedFields,
      hasHighOrCriticalRisk,
    }
  }, [job])

  const canCommit = fieldStats.needsReview === 0 && fieldStats.committable > 0 && commitState === 'idle'

  // ── Commit handler ───────────────────────────────────────────────────
  const handleCommit = useCallback(async () => {
    if (!jobId || !patientId || !consentToken) return
    setCommitState('committing')
    setCommitError(null)
    try {
      const result = await NexaApiClient.commitExtractionJob(
        jobId,
        { patient_id: patientId, encounter_summary: encounterSummary || undefined },
        consentToken,
      )
      setCommitResult(result)
      setCommitState('success')
    } catch (err) {
      setCommitState('error')
      if (err instanceof ApiError) {
        if (err.status === 401) {
          router.push('/doctor/login')
          return
        }
        if (err.status === 409) {
          setCommitError(
            'Review incomplete: job contains unresolved fields needing review. ' +
            'Please return to the Review Cockpit and adjudicate all remaining fields.',
          )
        } else if (err.status === 400) {
          setCommitError(err.message || 'Invalid commit request.')
        } else if (err.status === 403) {
          setCommitError('Consent required for pipeline commit.')
        } else {
          setCommitError(err.message || 'Commit failed. Please try again.')
        }
      } else {
        setCommitError('Network error. Please try again.')
      }
    }
  }, [jobId, patientId, consentToken, encounterSummary, router])

  // ── Loading state ────────────────────────────────────────────────────
  if (loading) {
    return (
      <YStack flex={1} backgroundColor="$background" justifyContent="center" alignItems="center" gap="$4" padding="$6">
        <Spinner size="large" color="$blue10" />
        <Text color="$color10" fontSize="$3">Loading commit summary…</Text>
      </YStack>
    )
  }

  // ── Fetch error ──────────────────────────────────────────────────────
  if (fetchError && !job) {
    return (
      <YStack flex={1} backgroundColor="$background" justifyContent="center" alignItems="center" gap="$4" padding="$6">
        <Text color="$red10" fontSize="$5">{fetchError}</Text>
        <Button theme="blue" onPress={fetchJob}>Retry</Button>
      </YStack>
    )
  }

  // ── Success state ────────────────────────────────────────────────────
  if (commitState === 'success' && commitResult) {
    return (
      <YStack flex={1} backgroundColor="$background" justifyContent="center" alignItems="center" gap="$4" padding="$6" maxWidth={700} marginHorizontal="auto">
        <Text color="$green10" fontSize="$7">✓ Committed</Text>
        <Card backgroundColor="$green4" borderRadius="$4" padding="$6" gap="$3" width="100%">
          <Text color="$green10" fontSize="$5" fontWeight="600">
            {commitResult.committed_fields_count} field{commitResult.committed_fields_count !== 1 ? 's' : ''} committed to patient record
          </Text>
          <Separator />
          <YStack gap="$2">
            <Text color="$color10" fontSize="$2">
              Job: {commitResult.job_id}
            </Text>
            <Text color="$color10" fontSize="$2">
              Timeline Event: {commitResult.timeline_event_id}
            </Text>
            <Text color="$color10" fontSize="$2">
              Committed at: {new Date(commitResult.committed_at).toLocaleString()}
            </Text>
          </YStack>
        </Card>

        <XStack gap="$3" marginTop="$4">
          <Button
            theme="blue"
            size="$4"
            onPress={() => router.push('/doctor/pipeline/upload')}
          >
            Upload Another
          </Button>
          <Button
            chromeless
            size="$4"
            onPress={() => router.push('/doctor/dashboard')}
          >
            Back to Dashboard
          </Button>
        </XStack>
      </YStack>
    )
  }

  return (
    <YStack flex={1} backgroundColor="$background" padding="$6" gap="$4" maxWidth={900} marginHorizontal="auto">
      {/* ALPHA badge + header */}
      <XStack alignItems="center" gap="$2">
        <H2 color="$color12" fontSize="$7">Commit to Record</H2>
        <Card backgroundColor="$orange4" borderRadius="$4" paddingHorizontal="$2" paddingVertical="$1">
          <Text color="$orange10" fontSize="$2" fontWeight="700" textTransform="uppercase">
            ALPHA
          </Text>
        </Card>
      </XStack>

      <Paragraph color="$color10" fontSize="$3">
        ALPHA · AI-assisted extraction results require clinical verification
        before commitment.
      </Paragraph>

      <Separator />

      {/* Job info */}
      <Card padding="$4" backgroundColor="$backgroundHover" borderRadius="$4" gap="$2">
        <XStack justifyContent="space-between" alignItems="center">
          <YStack>
            <Text color="$color10" fontSize="$2" textTransform="uppercase">Job</Text>
            <Text color="$color12" fontSize="$4" fontWeight="600">
              {job?.job_id ?? '—'}
            </Text>
          </YStack>
          <YStack>
            <Text color="$color10" fontSize="$2" textTransform="uppercase">Patient</Text>
            <Text color="$color12" fontSize="$4" fontWeight="600">
              {patientId}
            </Text>
          </YStack>
          <YStack alignItems="flex-end">
            <Text color="$color10" fontSize="$2" textTransform="uppercase">Type</Text>
            <Text color="$color12" fontSize="$4">{job?.document_type ?? '—'}</Text>
          </YStack>
        </XStack>
      </Card>

      {/* Field summary cards */}
      <XStack gap="$3">
        <Card flex={1} padding="$3" backgroundColor="$green4" borderRadius="$4" alignItems="center">
          <Text color="$green10" fontSize="$6" fontWeight="700">{fieldStats.committable}</Text>
          <Text color="$green10" fontSize="$2">To Commit</Text>
        </Card>
        <Card flex={1} padding="$3" backgroundColor="$red4" borderRadius="$4" alignItems="center">
          <Text color="$red10" fontSize="$6" fontWeight="700">{fieldStats.rejected}</Text>
          <Text color="$red10" fontSize="$2">Rejected</Text>
        </Card>
        {fieldStats.needsReview > 0 && (
          <Card flex={1} padding="$3" backgroundColor="$orange4" borderRadius="$4" alignItems="center">
            <Text color="$orange10" fontSize="$6" fontWeight="700">{fieldStats.needsReview}</Text>
            <Text color="$orange10" fontSize="$2">Unresolved</Text>
          </Card>
        )}
      </XStack>

      {/* HIGH/CRITICAL risk warning banner */}
      {fieldStats.hasHighOrCriticalRisk && (
        <Card backgroundColor="$red4" borderRadius="$4" padding="$4" gap="$2">
          <Text color="$red10" fontSize="$4" fontWeight="600">
            ⚠ HIGH/CRITICAL Risk Fields Present
          </Text>
          <Paragraph color="$red10" fontSize="$3">
            This job contains fields flagged as HIGH or CRITICAL risk.
            Please double-check these fields before committing to the
            patient record. High-risk AI extractions may contain errors.
          </Paragraph>
        </Card>
      )}

      {/* Unresolved fields warning */}
      {fieldStats.needsReview > 0 && (
        <Card backgroundColor="$orange4" borderRadius="$4" padding="$4" gap="$2">
          <Text color="$orange10" fontSize="$4" fontWeight="600">
            ⚠ {fieldStats.needsReview} field{fieldStats.needsReview !== 1 ? 's' : ''} still need review before you can commit
          </Text>
          <Paragraph color="$orange10" fontSize="$3">
            All fields must be adjudicated before committing. Return to the
            Review Cockpit to approve, edit, or reject remaining fields.
          </Paragraph>
          <Button
            theme="orange"
            size="$3"
            onPress={() =>
              router.push(
                `/doctor/pipeline/review/${jobId}?patient_id=${patientId}&workflow_id=${workflowId}`,
              )
            }
          >
            Go to Review Cockpit
          </Button>
        </Card>
      )}

      {/* Field list grouped by status */}
      <ScrollView>
        <YStack gap="$2">
          {/* Legacy auto-approved rows are never committable. */}
          {fieldStats.autoApproved.length > 0 && (
            <YStack gap="$2">
              <Text color="$color12" fontSize="$3" fontWeight="600">
                Legacy auto-approved — blocked ({fieldStats.autoApproved.length})
              </Text>
              {fieldStats.autoApproved.map((field) => (
                <FieldSummaryRow key={field.field_id} field={field} />
              ))}
            </YStack>
          )}

          {/* Human-approved section */}
          {fieldStats.humanApproved.length > 0 && (
            <YStack gap="$2" marginTop="$2">
              <Text color="$color12" fontSize="$3" fontWeight="600">
                Clinician Verified ({fieldStats.humanApproved.length})
              </Text>
              {fieldStats.humanApproved.map((field) => (
                <FieldSummaryRow key={field.field_id} field={field} />
              ))}
            </YStack>
          )}

          {/* Edited section */}
          {fieldStats.edited.length > 0 && (
            <YStack gap="$2" marginTop="$2">
              <Text color="$color12" fontSize="$3" fontWeight="600">
                Edited ({fieldStats.edited.length})
              </Text>
              {fieldStats.edited.map((field) => (
                <FieldSummaryRow key={field.field_id} field={field} />
              ))}
            </YStack>
          )}

          {/* Rejected fields */}
          {fieldStats.rejectedFields.length > 0 && (
            <YStack gap="$2" marginTop="$2">
              <Text color="$color10" fontSize="$2" textTransform="uppercase">
                Rejected — will NOT be committed ({fieldStats.rejectedFields.length})
              </Text>
              {fieldStats.rejectedFields.map((field) => (
                <Card key={field.field_id} backgroundColor="$background" borderRadius="$3" padding="$3" opacity={0.5}>
                  <XStack justifyContent="space-between" alignItems="center">
                    <YStack gap="$1">
                      <Text color="$color10" fontSize="$3" textDecorationLine="line-through">
                        {field.field_name}
                      </Text>
                      <Text color="$color10" fontSize="$3" textDecorationLine="line-through">
                        {field.raw_value}
                      </Text>
                    </YStack>
                    <CommitSafetyBadge status="rejected" />
                  </XStack>
                </Card>
              ))}
            </YStack>
          )}

          {/* Unresolved fields */}
          {fieldStats.unresolvedFields.length > 0 && (
            <YStack gap="$2" marginTop="$2">
              <Text color="$orange10" fontSize="$2" textTransform="uppercase">
                Unresolved — blocking commit ({fieldStats.unresolvedFields.length})
              </Text>
              {fieldStats.unresolvedFields.map((field) => (
                <Card key={field.field_id} backgroundColor="$orange2" borderRadius="$3" padding="$3" borderWidth={1} borderColor="$orange5">
                  <XStack justifyContent="space-between" alignItems="center">
                    <YStack gap="$1">
                      <Text color="$color12" fontSize="$3">
                        {field.field_name}
                      </Text>
                      <Text color="$color12" fontSize="$3">
                        {field.raw_value}
                      </Text>
                    </YStack>
                    <CommitSafetyBadge status="needs_review" />
                  </XStack>
                </Card>
              ))}
            </YStack>
          )}
        </YStack>
      </ScrollView>

      <Separator />

      {/* Encounter summary */}
      <YStack gap="$2">
        <Text color="$color12" fontSize="$3" fontWeight="600">Encounter Summary (optional)</Text>
        <Input
          value={encounterSummary}
          onChangeText={setEncounterSummary}
          placeholder="Brief summary of the clinical encounter…"
          size="$4"
        />
      </YStack>

      {/* Commit error */}
      {commitError && (
        <Card backgroundColor="$red4" borderRadius="$3" padding="$4" gap="$2">
          <Text color="$red10" fontSize="$4" fontWeight="600">{commitError}</Text>
          <Button
            size="$2"
            chromeless
            onPress={() => { setCommitState('idle'); setCommitError(null) }}
          >
            Dismiss
          </Button>
        </Card>
      )}

      {/* Action buttons */}
      <XStack justifyContent="space-between" alignItems="center">
        <Button
          chromeless
          onPress={() =>
            router.push(
              `/doctor/pipeline/review/${jobId}?patient_id=${patientId}&workflow_id=${workflowId}`,
            )
          }
        >
          ← Back to Review
        </Button>

        {commitState === 'committing' ? (
          <XStack alignItems="center" gap="$3">
            <Spinner size="small" color="$blue10" />
            <Text color="$color10" fontSize="$3">Committing to patient record…</Text>
          </XStack>
        ) : (
          <Button
            theme="green"
            size="$4"
            disabled={!canCommit}
            onPress={handleCommit}
          >
            {canCommit
              ? `Commit ${fieldStats.committable} Field${fieldStats.committable !== 1 ? 's' : ''} to Record`
              : fieldStats.needsReview > 0
                ? `${fieldStats.needsReview} field${fieldStats.needsReview !== 1 ? 's' : ''} still need review`
                : 'No fields to commit'}
          </Button>
        )}
      </XStack>
    </YStack>
  )
}

// ── FieldSummaryRow — single field row in the commit summary ───────────

function FieldSummaryRow({ field }: { field: ExtractedField }) {
  return (
    <Card backgroundColor="$backgroundHover" borderRadius="$3" padding="$3">
      <XStack justifyContent="space-between" alignItems="center">
        <YStack gap="$1" flex={1}>
          <Text color="$color12" fontSize="$3" fontWeight="600">
            {field.field_name}
          </Text>
          <Text color="$color12" fontSize="$3">
            {field.corrected_value ?? field.raw_value}
          </Text>
          {field.corrected_value && field.corrected_value !== field.raw_value && (
            <Text color="$color10" fontSize="$2">
              Original: {field.raw_value}
            </Text>
          )}
        </YStack>
        <XStack gap="$2" alignItems="center">
          <Text color="$color10" fontSize="$2">
            {Math.round(field.confidence * 100)}%
          </Text>
          <Card
            backgroundColor={
              field.risk_level === 'LOW_RISK' ? '$green4' :
              field.risk_level === 'MEDIUM_RISK' ? '$orange4' :
              '$red4'
            }
            borderRadius="$4"
            paddingHorizontal="$2"
            paddingVertical="$1"
          >
            <Text
              color={
                field.risk_level === 'LOW_RISK' ? '$green10' :
                field.risk_level === 'MEDIUM_RISK' ? '$orange10' :
                '$red10'
              }
              fontSize="$1"
              fontWeight="600"
            >
              {field.risk_level.replace('_', ' ')}
            </Text>
          </Card>
          <CommitSafetyBadge status={field.status} />
        </XStack>
      </XStack>
    </Card>
  )
}