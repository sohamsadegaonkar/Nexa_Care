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
 * Route: /doctor/pipeline/jobs/[jobId]?consent_token=...&patient_id=...
 */

'use client'

import {
  YStack, H2, Button, Text, Spinner, Card, XStack, Separator, Progress,
} from '@my/ui'
import { useState, useEffect, useRef, useCallback } from 'react'
import { useRouter, useSearchParams, useParams } from 'next/navigation'
import { NexaApiClient, type ExtractionJobStatusResponse, ApiError } from '../../utils/apiClient'
import { useProviderAuth } from '../doctor/ProviderAuthContext'

/** Polling interval in milliseconds. */
const POLL_INTERVAL_MS = 2_000

/** Terminal job statuses — polling stops when reached. */
const TERMINAL_STATUSES = ['auto_approved', 'review_required', 'review_pending', 'failed', 'committed']

/**
 * Display labels for job status lifecycle.
 */
const STATUS_DISPLAY: Record<string, { label: string; icon: string; color: string }> = {
  queued: { label: 'Queued', icon: '⏳', color: '$blue10' },
  processing: { label: 'Extracting', icon: '⚙️', color: '$orange10' },
  extracting: { label: 'Extracting', icon: '⚙️', color: '$orange10' },
  scored: { label: 'Scored', icon: '✅', color: '$green10' },
  review_required: { label: 'Review Pending', icon: '⚠️', color: '$orange10' },
  review_pending: { label: 'Review Pending', icon: '⚠️', color: '$orange10' },
  auto_approved: { label: 'Auto-Approved', icon: '✅', color: '$green10' },
  failed: { label: 'Failed', icon: '❌', color: '$red10' },
  committed: { label: 'Committed', icon: '📋', color: '$green10' },
}

/** Progress mapping: estimated completion percentage per status. */
const STATUS_PROGRESS: Record<string, number> = {
  queued: 10,
  processing: 40,
  extracting: 40,
  scored: 80,
  review_required: 90,
  review_pending: 90,
  auto_approved: 100,
  failed: 100,
  committed: 100,
}

export function JobStatusScreen() {
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
  const [error, setError] = useState<string | null>(null)
  const [pollingActive, setPollingActive] = useState(true)

  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // ── Session guard ────────────────────────────────────────────────────
  if (!isAuthenticated) {
    return (
      <YStack f={1} bg="$background" jc="center" ai="center" gap="$4" p="$6">
        <Text col="$red10" size="$6">🔒 Session Required</Text>
        <Text col="$colorSubdued" size="$3">
          Please log in to view job status.
        </Text>
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
        <Text col="$colorSubdued" size="$3">
          You must have an active consent grant to view pipeline job status.
        </Text>
        <Button theme="blue" onPress={() => router.push('/doctor/request-consent')}>
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
      <YStack f={1} bg="$background" jc="center" ai="center" gap="$4" p="$6">
        <Spinner size="large" color="$blue10" />
        <Text col="$colorSubdued" size="$3">Loading job status…</Text>
      </YStack>
    )
  }

  // ── Error state ──────────────────────────────────────────────────────
  if (error && !job) {
    return (
      <YStack f={1} bg="$background" jc="center" ai="center" gap="$4" p="$6">
        <Text col="$red10" size="$5">{error}</Text>
        <Button theme="blue" onPress={() => { setLoading(true); setError(null); fetchJob(); }}>
          Retry
        </Button>
      </YStack>
    )
  }

  const statusInfo = STATUS_DISPLAY[job?.status ?? ''] ?? STATUS_DISPLAY.queued
  const progressPct = STATUS_PROGRESS[job?.status ?? 'queued'] ?? 0
  const autoApproved = job?.extracted_fields.filter((f) => f.status === 'auto_approved').length ?? 0
  const needsReview = job?.extracted_fields.filter((f) => f.status === 'needs_review').length ?? 0
  const isReviewPending = job?.status === 'review_required' || job?.status === 'review_pending'
  const isAutoApproved = job?.status === 'auto_approved'
  const isFailed = job?.status === 'failed'

  return (
    <YStack f={1} bg="$background" p="$6" gap="$4" mw={900} mx="auto">
      {/* ALPHA badge + header */}
      <XStack ai="center" gap="$2">
        <H2 col="$color" size="$7">Extraction Job</H2>
        <Card bg="$orange4" br="$4" px="$2" py="$1">
          <Text col="$orange10" size="$2" fontWeight="700" textTransform="uppercase">
            ALPHA
          </Text>
        </Card>
      </XStack>

      <Text col="$colorSubdued" size="$3">
        ALPHA · AI-assisted extraction results require clinical verification
        before commitment.
      </Text>

      <Separator />

      {/* ── Status banner ──────────────────────────────────────────────── */}
      <Card
        p="$4"
        bg={
          isFailed ? '$red4' :
          isReviewPending ? '$orange4' :
          isAutoApproved ? '$green4' :
          '$blue4'
        }
        br="$4"
        gap="$2"
      >
        <XStack ai="center" gap="$3">
          <Text size="$6">{statusInfo.icon}</Text>
          <YStack gap="$1">
            <Text col={statusInfo.color} size="$5" fontWeight="700">
              {statusInfo.label}
            </Text>
            <Text col="$colorSubdued" size="$2">
              Job ID: {job?.job_id ?? '—'} · Type: {job?.document_type ?? '—'} · Patient: {patientId}
            </Text>
          </YStack>
        </XStack>
      </Card>

      {/* ── Progress bar ──────────────────────────────────────────────── */}
      <YStack gap="$2">
        <XStack jc="space-between" ai="center">
          <Text col="$color" size="$3" fontWeight="600">Extraction Progress</Text>
          <XStack ai="center" gap="$2">
            <Text col="$colorSubdued" size="$3">{progressPct}%</Text>
            {pollingActive && <Spinner size="small" color="$blue10" />}
          </XStack>
        </XStack>
        <Progress value={progressPct} size="$3">
          <Progress.Indicator animation="bouncy" />
        </Progress>
        {/* Status lifecycle dots */}
        <XStack jc="space-between" mt="$1">
          {['Queued', 'Extracting', 'Scored', 'Review'].map((step, idx) => {
            const thresholds = [10, 40, 80, 90]
            const active = progressPct >= thresholds[idx]
            return (
              <Text
                key={step}
                col={active ? '$blue10' : '$colorSubdued'}
                size="$1"
                fontWeight={active ? '700' : '400'}
              >
                {active ? '●' : '○'} {step}
              </Text>
            )
          })}
        </XStack>
      </YStack>

      {/* ── Field summary (shown when scored or beyond) ────────────────── */}
      {(autoApproved + needsReview) > 0 && (
        <YStack gap="$3">
          <Text col="$color" size="$3" fontWeight="600">Field Summary</Text>
          <XStack gap="$4">
            <Card f={1} p="$3" bg="$green4" br="$4" ai="center">
              <Text col="$green10" size="$6" fontWeight="700">{autoApproved}</Text>
              <Text col="$green10" size="$2">Auto-Approved</Text>
            </Card>
            <Card f={1} p="$3" bg="$orange4" br="$4" ai="center">
              <Text col="$orange10" size="$6" fontWeight="700">{needsReview}</Text>
              <Text col="$orange10" size="$2">Need Review</Text>
            </Card>
          </XStack>
          {job?.overall_confidence != null && (
            <Text col="$colorSubdued" size="$2">
              Overall confidence: {(job.overall_confidence * 100).toFixed(0)}%
            </Text>
          )}
        </YStack>
      )}

      {/* ── Polling indicator ──────────────────────────────────────────── */}
      {pollingActive && !error && (
        <XStack ai="center" gap="$2">
          <Spinner size="small" color="$blue10" />
          <Text col="$colorSubdued" size="$2">Polling every 2s for updates…</Text>
        </XStack>
      )}

      {/* ── Error (non-blocking) ───────────────────────────────────────── */}
      {error && job && (
        <Card bg="$red4" br="$3" p="$3">
          <Text col="$red10" size="$3">{error}</Text>
        </Card>
      )}

      {/* ── Navigation ────────────────────────────────────────────────── */}
      <Separator />

      <XStack jc="space-between" ai="center">
        <Button chromeless onPress={() => router.push('/doctor/pipeline/upload')}>
          ← Upload Another
        </Button>

        {isReviewPending && (
          <Button
            theme="orange"
            size="$4"
            onPress={() =>
              router.push(`/doctor/pipeline/review-queue?consent_token=${consentToken}`)
            }
          >
            Go to Review Queue →
          </Button>
        )}

        {isAutoApproved && (
          <Button
            theme="green"
            size="$4"
            onPress={() =>
              router.push(
                `/doctor/pipeline/commit/${job!.job_id}?patient_id=${patientId}&consent_token=${consentToken}`,
              )
            }
          >
            Commit to Record →
          </Button>
        )}

        {isFailed && (
          <Button
            theme="red"
            size="$4"
            onPress={() =>
              router.push(
                `/doctor/pipeline/upload?patient_id=${patientId}&consent_token=${consentToken}`,
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
