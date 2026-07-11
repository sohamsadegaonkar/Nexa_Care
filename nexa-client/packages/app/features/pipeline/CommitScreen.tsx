/**
 * Commit screen — final review summary and commit to patient record.
 *
 * Displays a summary of the extraction job grouped by field status:
 * auto-approved, human-approved (clinician-verified), edited, and rejected.
 * Commits only fields with status auto_approved, approved, or edited.
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
 * Route: /doctor/pipeline/commit/[jobId]?consent_token=...&patient_id=...
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

type CommitState = 'idle' | 'committing' | 'success' | 'error'

// ── CommitSafetyBadge — visual status indicator per field ───────────────

/**
 * CommitSafetyBadge — shows the provenance of a field in the commit summary.
 *
 * Four states:
 *   - auto_approved → green "Auto ✓" badge
 *   - approved      → blue "Verified ✓" badge (clinician-verified)
 *   - edited        → yellow "Edited ✎" badge
 *   - rejected      → red "✕ Excluded" badge
 */
function CommitSafetyBadge({ status }: { status: string }) {
  if (status === 'auto_approved') {
    return (
      <Card bg="$green4" br="$4" px="$2" py="$1">
        <Text col="$green10" size="$1" fontWeight="700">
          Auto ✓
        </Text>
      </Card>
    )
  }
  if (status === 'approved') {
    return (
      <Card bg="$blue4" br="$4" px="$2" py="$1">
        <Text col="$blue10" size="$1" fontWeight="700">
          Verified ✓
        </Text>
      </Card>
    )
  }
  if (status === 'edited') {
    return (
      <Card bg="$yellow4" br="$4" px="$2" py="$1">
        <Text col="$yellow10" size="$1" fontWeight="700">
          Edited ✎
        </Text>
      </Card>
    )
  }
  if (status === 'rejected') {
    return (
      <Card bg="$red4" br="$4" px="$2" py="$1">
        <Text col="$red10" size="$1" fontWeight="700">
          ✕ Excluded
        </Text>
      </Card>
    )
  }
  // needs_review — unresolved
  return (
    <Card bg="$orange4" br="$4" px="$2" py="$1">
      <Text col="$orange10" size="$1" fontWeight="700">
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
  const consentToken = searchParams.get('consent_token') ?? ''

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
      <YStack f={1} bg="$background" jc="center" ai="center" gap="$4" p="$6">
        <Text col="$red10" size="$6">🔒 Session Required</Text>
        <Paragraph col="$colorSubdued" size="$3">
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
      <YStack f={1} bg="$background" jc="center" ai="center" gap="$4" p="$6">
        <Text col="$red10" size="$6">🔒 Consent Required</Text>
        <Paragraph col="$colorSubdued" size="$3">
          You must have an active consent grant to commit pipeline fields.
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
    const committableFields = [...autoApproved, ...humanApproved, ...edited]
    const hasHighOrCriticalRisk = committableFields.some(
      (f) => f.risk_level === 'HIGH_RISK' || f.risk_level === 'CRITICAL_RISK',
    )
    return {
      committable: committableFields.length,
      rejected: rejectedFields.length,
      needsReview: unresolvedFields.length,
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
      <YStack f={1} bg="$background" jc="center" ai="center" gap="$4" p="$6">
        <Spinner size="large" color="$blue10" />
        <Text col="$colorSubdued" size="$3">Loading commit summary…</Text>
      </YStack>
    )
  }

  // ── Fetch error ──────────────────────────────────────────────────────
  if (fetchError && !job) {
    return (
      <YStack f={1} bg="$background" jc="center" ai="center" gap="$4" p="$6">
        <Text col="$red10" size="$5">{fetchError}</Text>
        <Button theme="blue" onPress={fetchJob}>Retry</Button>
      </YStack>
    )
  }

  // ── Success state ────────────────────────────────────────────────────
  if (commitState === 'success' && commitResult) {
    return (
      <YStack f={1} bg="$background" jc="center" ai="center" gap="$4" p="$6" mw={700} mx="auto">
        <Text col="$green10" size="$7">✓ Committed</Text>
        <Card bg="$green4" br="$4" p="$6" gap="$3" w="100%">
          <Text col="$green10" size="$5" fontWeight="600">
            {commitResult.committed_fields_count} field{commitResult.committed_fields_count !== 1 ? 's' : ''} committed to patient record
          </Text>
          <Separator />
          <YStack gap="$2">
            <Text col="$colorSubdued" size="$2">
              Job: {commitResult.job_id}
            </Text>
            <Text col="$colorSubdued" size="$2">
              Timeline Event: {commitResult.timeline_event_id}
            </Text>
            <Text col="$colorSubdued" size="$2">
              Committed at: {new Date(commitResult.committed_at).toLocaleString()}
            </Text>
          </YStack>
        </Card>

        <XStack gap="$3" mt="$4">
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
    <YStack f={1} bg="$background" p="$6" gap="$4" mw={900} mx="auto">
      {/* ALPHA badge + header */}
      <XStack ai="center" gap="$2">
        <H2 col="$color" size="$7">Commit to Record</H2>
        <Card bg="$orange4" br="$4" px="$2" py="$1">
          <Text col="$orange10" size="$2" fontWeight="700" textTransform="uppercase">
            ALPHA
          </Text>
        </Card>
      </XStack>

      <Paragraph col="$colorSubdued" size="$3">
        ALPHA · AI-assisted extraction results require clinical verification
        before commitment.
      </Paragraph>

      <Separator />

      {/* Job info */}
      <Card p="$4" bg="$backgroundHover" br="$4" gap="$2">
        <XStack jc="space-between" ai="center">
          <YStack>
            <Text col="$colorSubdued" size="$2" textTransform="uppercase">Job</Text>
            <Text col="$color" size="$4" fontWeight="600" fontFamily="$mono">
              {job?.job_id ?? '—'}
            </Text>
          </YStack>
          <YStack>
            <Text col="$colorSubdued" size="$2" textTransform="uppercase">Patient</Text>
            <Text col="$color" size="$4" fontWeight="600" fontFamily="$mono">
              {patientId}
            </Text>
          </YStack>
          <YStack ai="flex-end">
            <Text col="$colorSubdued" size="$2" textTransform="uppercase">Type</Text>
            <Text col="$color" size="$4">{job?.document_type ?? '—'}</Text>
          </YStack>
        </XStack>
      </Card>

      {/* Field summary cards */}
      <XStack gap="$3">
        <Card f={1} p="$3" bg="$green4" br="$4" ai="center">
          <Text col="$green10" size="$6" fontWeight="700">{fieldStats.committable}</Text>
          <Text col="$green10" size="$2">To Commit</Text>
        </Card>
        <Card f={1} p="$3" bg="$red4" br="$4" ai="center">
          <Text col="$red10" size="$6" fontWeight="700">{fieldStats.rejected}</Text>
          <Text col="$red10" size="$2">Rejected</Text>
        </Card>
        {fieldStats.needsReview > 0 && (
          <Card f={1} p="$3" bg="$orange4" br="$4" ai="center">
            <Text col="$orange10" size="$6" fontWeight="700">{fieldStats.needsReview}</Text>
            <Text col="$orange10" size="$2">Unresolved</Text>
          </Card>
        )}
      </XStack>

      {/* HIGH/CRITICAL risk warning banner */}
      {fieldStats.hasHighOrCriticalRisk && (
        <Card bg="$red4" br="$4" p="$4" gap="$2">
          <Text col="$red10" size="$4" fontWeight="600">
            ⚠ HIGH/CRITICAL Risk Fields Present
          </Text>
          <Paragraph col="$red10" size="$3">
            This job contains fields flagged as HIGH or CRITICAL risk.
            Please double-check these fields before committing to the
            patient record. High-risk AI extractions may contain errors.
          </Paragraph>
        </Card>
      )}

      {/* Unresolved fields warning */}
      {fieldStats.needsReview > 0 && (
        <Card bg="$orange4" br="$4" p="$4" gap="$2">
          <Text col="$orange10" size="$4" fontWeight="600">
            ⚠ {fieldStats.needsReview} field{fieldStats.needsReview !== 1 ? 's' : ''} still need review before you can commit
          </Text>
          <Paragraph col="$orange10" size="$3">
            All fields must be adjudicated before committing. Return to the
            Review Cockpit to approve, edit, or reject remaining fields.
          </Paragraph>
          <Button
            theme="orange"
            size="$3"
            onPress={() =>
              router.push(
                `/doctor/pipeline/review/${jobId}?patient_id=${patientId}&consent_token=${consentToken}`,
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
          {/* Auto-approved section */}
          {fieldStats.autoApproved.length > 0 && (
            <YStack gap="$2">
              <Text col="$color" size="$3" fontWeight="600">
                Auto-Approved ({fieldStats.autoApproved.length})
              </Text>
              {fieldStats.autoApproved.map((field) => (
                <FieldSummaryRow key={field.field_id} field={field} />
              ))}
            </YStack>
          )}

          {/* Human-approved section */}
          {fieldStats.humanApproved.length > 0 && (
            <YStack gap="$2" mt="$2">
              <Text col="$color" size="$3" fontWeight="600">
                Clinician Verified ({fieldStats.humanApproved.length})
              </Text>
              {fieldStats.humanApproved.map((field) => (
                <FieldSummaryRow key={field.field_id} field={field} />
              ))}
            </YStack>
          )}

          {/* Edited section */}
          {fieldStats.edited.length > 0 && (
            <YStack gap="$2" mt="$2">
              <Text col="$color" size="$3" fontWeight="600">
                Edited ({fieldStats.edited.length})
              </Text>
              {fieldStats.edited.map((field) => (
                <FieldSummaryRow key={field.field_id} field={field} />
              ))}
            </YStack>
          )}

          {/* Rejected fields */}
          {fieldStats.rejectedFields.length > 0 && (
            <YStack gap="$2" mt="$2">
              <Text col="$colorSubdued" size="$2" textTransform="uppercase">
                Rejected — will NOT be committed ({fieldStats.rejectedFields.length})
              </Text>
              {fieldStats.rejectedFields.map((field) => (
                <Card key={field.field_id} bg="$background" br="$3" p="$3" opacity={0.5}>
                  <XStack jc="space-between" ai="center">
                    <YStack gap="$1">
                      <Text col="$colorSubdued" size="$3" fontFamily="$mono" textDecorationLine="line-through">
                        {field.field_name}
                      </Text>
                      <Text col="$colorSubdued" size="$3" textDecorationLine="line-through">
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
            <YStack gap="$2" mt="$2">
              <Text col="$orange10" size="$2" textTransform="uppercase">
                Unresolved — blocking commit ({fieldStats.unresolvedFields.length})
              </Text>
              {fieldStats.unresolvedFields.map((field) => (
                <Card key={field.field_id} bg="$orange2" br="$3" p="$3" borderWidth={1} borderColor="$orange5">
                  <XStack jc="space-between" ai="center">
                    <YStack gap="$1">
                      <Text col="$color" size="$3" fontFamily="$mono">
                        {field.field_name}
                      </Text>
                      <Text col="$color" size="$3">
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
        <Text col="$color" size="$3" fontWeight="600">Encounter Summary (optional)</Text>
        <Input
          value={encounterSummary}
          onChangeText={setEncounterSummary}
          placeholder="Brief summary of the clinical encounter…"
          size="$4"
        />
      </YStack>

      {/* Commit error */}
      {commitError && (
        <Card bg="$red4" br="$3" p="$4" gap="$2">
          <Text col="$red10" size="$4" fontWeight="600">{commitError}</Text>
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
      <XStack jc="space-between" ai="center">
        <Button
          chromeless
          onPress={() =>
            router.push(
              `/doctor/pipeline/review/${jobId}?patient_id=${patientId}&consent_token=${consentToken}`,
            )
          }
        >
          ← Back to Review
        </Button>

        {commitState === 'committing' ? (
          <XStack ai="center" gap="$3">
            <Spinner size="small" color="$blue10" />
            <Text col="$colorSubdued" size="$3">Committing to patient record…</Text>
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
    <Card bg="$backgroundHover" br="$3" p="$3">
      <XStack jc="space-between" ai="center">
        <YStack gap="$1" f={1}>
          <Text col="$color" size="$3" fontWeight="600" fontFamily="$mono">
            {field.field_name}
          </Text>
          <Text col="$color" size="$3">
            {field.corrected_value ?? field.raw_value}
          </Text>
          {field.corrected_value && field.corrected_value !== field.raw_value && (
            <Text col="$colorSubdued" size="$2">
              Original: {field.raw_value}
            </Text>
          )}
        </YStack>
        <XStack gap="$2" ai="center">
          <Text col="$colorSubdued" size="$2">
            {Math.round(field.confidence * 100)}%
          </Text>
          <Card
            bg={
              field.risk_level === 'LOW_RISK' ? '$green4' :
              field.risk_level === 'MEDIUM_RISK' ? '$orange4' :
              '$red4'
            }
            br="$4"
            px="$2"
            py="$1"
          >
            <Text
              col={
                field.risk_level === 'LOW_RISK' ? '$green10' :
                field.risk_level === 'MEDIUM_RISK' ? '$orange10' :
                '$red10'
              }
              size="$1"
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
