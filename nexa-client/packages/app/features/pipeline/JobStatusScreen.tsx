/**
 * Job status screen — monitor AI extraction job progress.
 *
 * Polls GET /api/v2/pipeline/jobs/{job_id} every 2 seconds.
 * Shows extraction progress: queued → extracting → scored → review_pending.
 * When scored: displays summary (N fields auto-approved, M need review).
 * When review_pending: shows "Go to Review Queue" button.
 *
 * ALPHA: This is an alpha implementation.
 *
 * SECURITY:
 * - All requests go through the shared NexaApiClient — no raw fetch/axios.
 * - Consent token passed as X-Consent-Token header.
 * - Session guard: must be authenticated.
 * - Stops polling on terminal states; cleans up on unmount.
 *
 * Route: /doctor/pipeline/jobs/[jobId]?workflow_id=...&patient_id=...
 */

'use client'

import { YStack, H2, Button, Text, Spinner, Card, XStack, Separator, Progress } from '@my/ui'
import { useState, useEffect, useRef, useCallback } from 'react'
import { useRouter, useSearchParams, useParams } from 'next/navigation'
import { NexaApiClient, type ExtractionJobStatusResponse, ApiError } from '../../utils/apiClient'
import { useProviderAuth } from '../doctor/ProviderAuthContext'
import { useCapability } from '../../services/capabilityStore'

/** Polling interval in milliseconds. */
const POLL_INTERVAL_MS = 2_000

/** Terminal job statuses — polling stops when reached. */
const TERMINAL_STATUSES = [
  'auto_approved',
  'review_required',
  'review_pending',
  'failed',
  'committed',
  'source_only',
  'quarantined',
]

/**
 * Display labels for job status lifecycle.
 */
type StatusColor = '$blue10' | '$orange10' | '$green10' | '$red10'

const STATUS_DISPLAY: Record<string, { label: string; icon: string; color: StatusColor }> = {
  queued: { label: 'Queued', icon: '⏳', color: '$blue10' },
  processing: { label: 'Extracting', icon: '⚙️', color: '$orange10' },
  extracting: { label: 'Extracting', icon: '⚙️', color: '$orange10' },
  scored: { label: 'Scored', icon: '✅', color: '$green10' },
  review_required: { label: 'Review Pending', icon: '⚠️', color: '$orange10' },
  review_pending: { label: 'Review Pending', icon: '⚠️', color: '$orange10' },
  auto_approved: { label: 'Legacy Unsafe State', icon: '⛔', color: '$red10' },
  failed: { label: 'Failed', icon: '❌', color: '$red10' },
  committed: { label: 'Committed', icon: '📋', color: '$green10' },
  source_only: { label: 'Source review required', icon: '📄', color: '$orange10' },
  quarantined: { label: 'Quarantined', icon: '⛔', color: '$red10' },
}

/** Progress mapping: estimated completion percentage per status. */
const STATUS_PROGRESS: Record<string, number> = {
  queued: 10,
  processing: 40,
  extracting: 40,
  scored: 80,
  review_required: 90,
  review_pending: 90,
  auto_approved: 90,
  failed: 100,
  committed: 100,
  source_only: 100,
  quarantined: 100,
}

export function JobStatusScreen() {
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
  const [error, setError] = useState<string | null>(null)
  const [pollingActive, setPollingActive] = useState(true)

  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // ── Session guard ────────────────────────────────────────────────────
  if (!isAuthenticated) {
    return (
      <YStack
        flex={1}
        backgroundColor="$background"
        justifyContent="center"
        alignItems="center"
        gap="$4"
        padding="$6"
      >
        <Text
          color="$red10"
          fontSize="$6"
        >
          🔒 Session Required
        </Text>
        <Text
          color="$color10"
          fontSize="$3"
        >
          Please log in to view job status.
        </Text>
        <Button
          theme="blue"
          onPress={() => router.push('/doctor/login')}
        >
          Go to Login
        </Button>
      </YStack>
    )
  }

  // ── Missing consent token guard ──────────────────────────────────────
  if (!consentToken) {
    return (
      <YStack
        flex={1}
        backgroundColor="$background"
        justifyContent="center"
        alignItems="center"
        gap="$4"
        padding="$6"
      >
        <Text
          color="$red10"
          fontSize="$6"
        >
          🔒 Consent Required
        </Text>
        <Text
          color="$color10"
          fontSize="$3"
        >
          {workflowId
            ? 'Access session expired — request access again.'
            : 'You must have an active consent grant to view pipeline job status.'}
        </Text>
        <Button
          theme="blue"
          onPress={() => router.push('/doctor/request-consent')}
        >
          Request Consent
        </Button>
      </YStack>
    )
  }

  // ── Fetch job status ─────────────────────────────────────────────────
  const fetchJob = useCallback(async () => {
    if (!jobId || !consentToken) return
    try {
      const data = await NexaApiClient.getExtractionJobStatus(jobId, consentToken)
      setJob(data)
      setError(null)

      if (TERMINAL_STATUSES.includes(data.status)) {
        setPollingActive(false)
      }
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 401) {
          router.push('/doctor/login')
          return
        }
        if (err.status === 403) {
          setError('Consent required to view job status.')
          setPollingActive(false)
          return
        }
        if (err.status === 404) {
          setError('Job not found or has expired.')
          setPollingActive(false)
          return
        }
      }
      setError('Unable to fetch job status. Retrying…')
    } finally {
      setLoading(false)
    }
  }, [jobId, consentToken, router])

  // ── Polling effect — every 2 seconds ─────────────────────────────────
  useEffect(() => {
    if (!pollingActive || !jobId) return

    fetchJob()

    const startPolling = () => {
      pollTimerRef.current = setTimeout(async () => {
        await fetchJob()
        if (pollingActive) startPolling()
      }, POLL_INTERVAL_MS)
    }

    startPolling()

    return () => {
      if (pollTimerRef.current) {
        clearTimeout(pollTimerRef.current)
        pollTimerRef.current = null
      }
    }
  }, [pollingActive, jobId, fetchJob])

  // ── Loading state ────────────────────────────────────────────────────
  if (loading && !job) {
    return (
      <YStack
        flex={1}
        backgroundColor="$background"
        justifyContent="center"
        alignItems="center"
        gap="$4"
        padding="$6"
      >
        <Spinner
          size="large"
          color="$blue10"
        />
        <Text
          color="$color10"
          fontSize="$3"
        >
          Loading job status…
        </Text>
      </YStack>
    )
  }

  // ── Error state ──────────────────────────────────────────────────────
  if (error && !job) {
    return (
      <YStack
        flex={1}
        backgroundColor="$background"
        justifyContent="center"
        alignItems="center"
        gap="$4"
        padding="$6"
      >
        <Text
          color="$red10"
          fontSize="$5"
        >
          {error}
        </Text>
        <Button
          theme="blue"
          onPress={() => {
            setLoading(true)
            setError(null)
            fetchJob()
          }}
        >
          Retry
        </Button>
      </YStack>
    )
  }

  const statusInfo = STATUS_DISPLAY[job?.status ?? ''] ?? STATUS_DISPLAY.queued
  const progressPct = STATUS_PROGRESS[job?.status ?? 'queued'] ?? 0
  const autoApproved = job?.extracted_fields.filter((f) => f.status === 'auto_approved').length ?? 0
  const needsReview = job?.extracted_fields.filter((f) => f.status === 'needs_review').length ?? 0
  const isReviewPending = job?.status === 'review_required'
  const hasLegacyAutoApproval = job?.status === 'auto_approved'
  const requiresSourceAdjudication = job?.status === 'source_only'
  const isQuarantined = job?.status === 'quarantined'
  const isFailed = job?.status === 'failed'

  return (
    <YStack
      flex={1}
      backgroundColor="$background"
      padding="$6"
      gap="$4"
      maxWidth={900}
      marginHorizontal="auto"
    >
      {/* ALPHA badge + header */}
      <XStack
        alignItems="center"
        gap="$2"
      >
        <H2
          color="$color12"
          fontSize="$7"
        >
          Extraction Job
        </H2>
        <Card
          backgroundColor="$orange4"
          borderRadius="$4"
          paddingHorizontal="$2"
          paddingVertical="$1"
        >
          <Text
            color="$orange10"
            fontSize="$2"
            fontWeight="700"
            textTransform="uppercase"
          >
            ALPHA
          </Text>
        </Card>
      </XStack>

      <Text
        color="$color10"
        fontSize="$3"
      >
        ALPHA · AI-assisted extraction results require clinical verification before commitment.
      </Text>

      <Separator />

      {/* ── Status banner ──────────────────────────────────────────────── */}
      <Card
        padding="$4"
        backgroundColor={
          isFailed
            ? '$red4'
            : isReviewPending
              ? '$orange4'
              : hasLegacyAutoApproval
                ? '$red4'
                : '$blue4'
        }
        borderRadius="$4"
        gap="$2"
      >
        <XStack
          alignItems="center"
          gap="$3"
        >
          <Text fontSize="$6">{statusInfo.icon}</Text>
          <YStack gap="$1">
            <Text
              color={statusInfo.color}
              fontSize="$5"
              fontWeight="700"
            >
              {statusInfo.label}
            </Text>
            <Text
              color="$color10"
              fontSize="$2"
            >
              Job ID: {job?.job_id ?? '—'} · Type: {job?.document_type ?? '—'} · Patient:{' '}
              {patientId}
            </Text>
          </YStack>
        </XStack>
      </Card>

      {/* ── Progress bar ──────────────────────────────────────────────── */}
      <YStack gap="$2">
        <XStack
          justifyContent="space-between"
          alignItems="center"
        >
          <Text
            color="$color12"
            fontSize="$3"
            fontWeight="600"
          >
            Extraction Progress
          </Text>
          <XStack
            alignItems="center"
            gap="$2"
          >
            <Text
              color="$color10"
              fontSize="$3"
            >
              {progressPct}%
            </Text>
            {pollingActive && (
              <Spinner
                size="small"
                color="$blue10"
              />
            )}
          </XStack>
        </XStack>
        <Progress
          value={progressPct}
          size="$3"
        >
          <Progress.Indicator />
        </Progress>
        {/* Status lifecycle dots */}
        <XStack
          justifyContent="space-between"
          marginTop="$1"
        >
          {['Queued', 'Extracting', 'Scored', 'Review'].map((step, idx) => {
            const thresholds = [10, 40, 80, 90]
            const active = progressPct >= thresholds[idx]
            return (
              <Text
                key={step}
                color={active ? '$blue10' : '$color10'}
                fontSize="$1"
                fontWeight={active ? '700' : '400'}
              >
                {active ? '●' : '○'} {step}
              </Text>
            )
          })}
        </XStack>
      </YStack>

      {/* ── Field summary (shown when scored or beyond) ────────────────── */}
      {autoApproved + needsReview > 0 && (
        <YStack gap="$3">
          <Text
            color="$color12"
            fontSize="$3"
            fontWeight="600"
          >
            Field Summary
          </Text>
          <XStack gap="$4">
            <Card
              flex={1}
              padding="$3"
              backgroundColor="$red4"
              borderRadius="$4"
              alignItems="center"
            >
              <Text
                color="$red10"
                fontSize="$6"
                fontWeight="700"
              >
                {autoApproved}
              </Text>
              <Text
                color="$red10"
                fontSize="$2"
              >
                Legacy blocked
              </Text>
            </Card>
            <Card
              flex={1}
              padding="$3"
              backgroundColor="$orange4"
              borderRadius="$4"
              alignItems="center"
            >
              <Text
                color="$orange10"
                fontSize="$6"
                fontWeight="700"
              >
                {needsReview}
              </Text>
              <Text
                color="$orange10"
                fontSize="$2"
              >
                Need Review
              </Text>
            </Card>
          </XStack>
          {job?.overall_confidence != null && (
            <Text
              color="$color10"
              fontSize="$2"
            >
              Overall confidence: {(job.overall_confidence * 100).toFixed(0)}%
            </Text>
          )}
        </YStack>
      )}

      {/* ── Polling indicator ──────────────────────────────────────────── */}
      {pollingActive && !error && (
        <XStack
          alignItems="center"
          gap="$2"
        >
          <Spinner
            size="small"
            color="$blue10"
          />
          <Text
            color="$color10"
            fontSize="$2"
          >
            Polling every 2s for updates…
          </Text>
        </XStack>
      )}

      {/* ── Error (non-blocking) ───────────────────────────────────────── */}
      {error && job && (
        <Card
          backgroundColor="$red4"
          borderRadius="$3"
          padding="$3"
        >
          <Text
            color="$red10"
            fontSize="$3"
          >
            {error}
          </Text>
        </Card>
      )}

      {/* ── Navigation ────────────────────────────────────────────────── */}
      <Separator />

      <XStack
        justifyContent="space-between"
        alignItems="center"
      >
        <Button
          chromeless
          onPress={() => router.push('/doctor/pipeline/upload')}
        >
          ← Upload Another
        </Button>

        {isReviewPending && (
          <Button
            theme="orange"
            size="$4"
            onPress={() => router.push(`/doctor/pipeline/review-queue?workflow_id=${workflowId}`)}
          >
            Go to Review Queue →
          </Button>
        )}

        {hasLegacyAutoApproval && (
          <Text
            color="$red10"
            fontSize="$3"
          >
            This legacy job is blocked. Quarantine and reprocess it before clinical review.
          </Text>
        )}

        {requiresSourceAdjudication && (
          <Button
            theme="orange"
            size="$4"
            onPress={() => router.push('/doctor/pipeline/adjudication')}
          >
            Open source adjudication
          </Button>
        )}

        {isQuarantined && (
          <Text
            color="$red10"
            fontSize="$3"
          >
            Quarantined jobs cannot enter ordinary adjudication.
          </Text>
        )}

        {isFailed && (
          <Button
            theme="red"
            size="$4"
            onPress={() =>
              router.push(
                `/doctor/pipeline/upload?patient_id=${patientId}&workflow_id=${workflowId}`
              )
            }
          >
            Re-upload Document
          </Button>
        )}
      </XStack>
    </YStack>
  )
}
