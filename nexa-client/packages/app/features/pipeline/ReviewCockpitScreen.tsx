/**
 * Review cockpit screen — side-by-side document preview + field cards.
 *
 * Layout:
 *   Left:  DocumentPreview — SVG page canvas with bounding-box overlays
 *          for AI-extracted fields. When a field card is hovered or
 *          selected, its source bounding box highlights on the preview.
 *          Clicking a bbox region scrolls the field card list to that field.
 *   Right: Scrollable list of FieldCard components for human adjudication.
 *
 * ALPHA: This is an alpha implementation. The document preview renders
 * bounding-box overlays on a placeholder page surface. When S3 presigned
 * URLs or a document rendering service is available, the placeholder will
 * be replaced with the actual page image. Source bounding boxes use the
 * normalized [x, y, width, height] coordinates from the backend
 * ExtractedFieldRecord.source_bbox field.
 *
 * SECURITY:
 * - All requests go through the shared NexaApiClient — no raw fetch/axios.
 * - Consent token passed as X-Consent-Token header on every API call.
 * - No hardcoded patient_id or provider_id.
 * - Session guard: must be authenticated.
 * - Frontend field status tracking is UX-only — backend validates on commit.
 *
 * Route: /doctor/pipeline/review/[jobId]?consent_token=...&patient_id=...
 */

'use client'

import {
  YStack, H2, Button, Text, Spinner, Card, XStack, Separator, ScrollView, Progress,
} from '@my/ui'
import { useState, useEffect, useCallback, useMemo } from 'react'
import { useRouter, useSearchParams, useParams } from 'next/navigation'
import { NexaApiClient, type ExtractionJobStatusResponse, type ExtractedField, ApiError } from '../../utils/apiClient'
import { useProviderAuth } from '../doctor/ProviderAuthContext'
import { FieldCard } from './FieldCard'
import { DocumentPreview, type BBoxField } from './DocumentPreview'

export function ReviewCockpitScreen() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const routeParams = useParams()
  const { isAuthenticated } = useProviderAuth()

  // jobId comes from the [jobId] route param (camelCase); snake_case only in API payloads
  const jobId = (routeParams.jobId as string) ?? ''
  const patientId = searchParams.get('patient_id') ?? ''
  const consentToken = searchParams.get('consent_token') ?? ''

  const [job, setJob] = useState<ExtractionJobStatusResponse | null>(null)
  const [fields, setFields] = useState<ExtractedField[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [currentPage, setCurrentPage] = useState(1)
  const [highlightedFieldId, setHighlightedFieldId] = useState<string | null>(null)

  // ── Session guard ────────────────────────────────────────────────────
  if (!isAuthenticated) {
    return (
      <YStack flex={1} backgroundColor="$background" justifyContent="center" alignItems="center" gap="$4" padding="$6">
        <Text color="$red10" fontSize="$6">🔒 Session Required</Text>
        <Text color="$color10" fontSize="$3">
          Please log in to review fields.
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
      <YStack flex={1} backgroundColor="$background" justifyContent="center" alignItems="center" gap="$4" padding="$6">
        <Text color="$red10" fontSize="$6">🔒 Consent Required</Text>
        <Text color="$color10" fontSize="$3">
          You must have an active consent grant to review fields.
        </Text>
        <Button theme="blue" onPress={() => router.push('/doctor/request-consent')}>
          Request Consent
        </Button>
      </YStack>
    )
  }

  // ── Fetch job + fields ───────────────────────────────────────────────
  const fetchJob = useCallback(async () => {
    if (!jobId || !consentToken) return
    setLoading(true)
    setError(null)
    try {
      const data = await NexaApiClient.getExtractionJobStatus(jobId, consentToken)
      setJob(data)
      setFields(data.extracted_fields)
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 401) {
          router.push('/doctor/login')
          return
        }
        if (err.status === 403) {
          setError('Consent required to view job details.')
          return
        }
        if (err.status === 404) {
          setError('Job not found.')
          return
        }
      }
      setError('Failed to load job details.')
    } finally {
      setLoading(false)
    }
  }, [jobId, consentToken, router])

  useEffect(() => {
    fetchJob()
  }, [fetchJob])

  // ── Computed: review progress ────────────────────────────────────────
  const reviewStats = useMemo(() => {
    const needsReview = fields.filter((f) => f.status === 'needs_review')
    const adjudicated = fields.filter(
      (f) => f.status === 'approved' || f.status === 'edited' || f.status === 'rejected',
    )
    const autoApproved = fields.filter((f) => f.status === 'auto_approved')
    const totalReviewable = needsReview.length + adjudicated.length + autoApproved.length
    return {
      total: fields.length,
      needsReview: needsReview.length,
      adjudicated: adjudicated.length,
      autoApproved: autoApproved.length,
      allReviewed: needsReview.length === 0 && autoApproved.length === 0,
      progressPct: totalReviewable > 0 ? Math.round((adjudicated.length / totalReviewable) * 100) : 100,
    }
  }, [fields])

  // ── Computed: total pages from field data ────────────────────────────
  const totalPages = useMemo(() => {
    if (fields.length === 0) return 1
    const maxPage = Math.max(...fields.map((f) => f.source_page))
    return Math.max(1, maxPage)
  }, [fields])

  // ── Computed: bbox fields for DocumentPreview ────────────────────────
  const bboxFields: BBoxField[] = useMemo(
    () =>
      fields.map((f) => ({
        field_id: f.field_id,
        field_name: f.field_name,
        source_page: f.source_page,
        source_bbox: f.source_bbox,
        status: f.status,
        risk_level: f.risk_level,
      })),
    [fields],
  )

  // ── Field update handler ─────────────────────────────────────────────
  const handleFieldUpdated = useCallback(
    (fieldId: string, newStatus: string, _finalValue: string) => {
      setFields((prev) =>
        prev.map((f) =>
          f.field_id === fieldId
            ? { ...f, status: newStatus as ExtractedField['status'] }
            : f,
        ),
      )
    },
    [],
  )

  // ── Source page click handler — from FieldCard → jump preview to page ─
  const handleSourcePageClick = useCallback((page: number) => {
    setCurrentPage(page)
  }, [])

  // ── Highlight field — from FieldCard hover/focus ─────────────────────
  const handleFieldHighlight = useCallback((fieldId: string | null) => {
    setHighlightedFieldId(fieldId)
  }, [])

  // ── Bbox click handler — from DocumentPreview → scroll to field card ──
  const handleBboxFieldClick = useCallback((fieldId: string) => {
    setHighlightedFieldId(fieldId)
    const el = document.getElementById(`field-card-${fieldId}`)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }, [])

  // ── Loading state ────────────────────────────────────────────────────
  if (loading) {
    return (
      <YStack flex={1} backgroundColor="$background" justifyContent="center" alignItems="center" gap="$4" padding="$6">
        <Spinner size="large" color="$blue10" />
        <Text color="$color10" fontSize="$3">Loading job details…</Text>
        <Button chromeless size="$2" onPress={fetchJob}>Retry</Button>
      </YStack>
    )
  }

  // ── Error state ──────────────────────────────────────────────────────
  if (error && !job) {
    return (
      <YStack flex={1} backgroundColor="$background" justifyContent="center" alignItems="center" gap="$4" padding="$6">
        <Text color="$red10" fontSize="$5">{error}</Text>
        <Button theme="blue" onPress={fetchJob}>Retry</Button>
      </YStack>
    )
  }

  return (
    <YStack flex={1} backgroundColor="$background" height="100vh">
      {/* ── Header bar ───────────────────────────────────────────────── */}
      <XStack
        padding="$3"
        backgroundColor="$backgroundHover"
        alignItems="center"
        justifyContent="space-between"
        borderBottomWidth={1}
        borderBottomColor="$borderColor"
      >
        <XStack alignItems="center" gap="$3">
          <H2 color="$color12" fontSize="$5">Review Cockpit</H2>
          <Card backgroundColor="$orange4" borderRadius="$4" paddingHorizontal="$2" paddingVertical="$1">
            <Text color="$orange10" fontSize="$1" fontWeight="700" textTransform="uppercase">
              ALPHA
            </Text>
          </Card>
        </XStack>

        <XStack alignItems="center" gap="$3">
          <Text color="$color10" fontSize="$2">
            Job: {job?.job_id ?? '—'}
          </Text>
          <Text color="$color10" fontSize="$2">
            Patient: {patientId}
          </Text>
          <Text color="$color10" fontSize="$2">
            Confidence: {((job?.overall_confidence ?? 0) * 100).toFixed(0)}%
          </Text>
        </XStack>
      </XStack>

      {/* ALPHA notice */}
      <YStack backgroundColor="$orange2" padding="$2" alignItems="center">
        <Text color="$orange10" fontSize="$2">
          ALPHA · AI-assisted extraction results require clinical verification
          before commitment.
        </Text>
      </YStack>

      {/* ── Review progress bar ──────────────────────────────────────── */}
      <YStack paddingHorizontal="$4" paddingVertical="$2" gap="$1">
        <XStack justifyContent="space-between" alignItems="center">
          <Text color="$color12" fontSize="$2" fontWeight="600">
            Review Progress
          </Text>
          <XStack alignItems="center" gap="$2">
            <Text color={
              reviewStats.allReviewed ? '$green10' : '$orange10'
            } fontSize="$2" fontWeight="600">
              {reviewStats.adjudicated}/{reviewStats.adjudicated + reviewStats.needsReview} reviewed
            </Text>
            {reviewStats.allReviewed && (
              <Card backgroundColor="$green4" borderRadius="$4" paddingHorizontal="$2" paddingVertical="$1">
                <Text color="$green10" fontSize="$1" fontWeight="700">
                  ✓ All Reviewed
                </Text>
              </Card>
            )}
          </XStack>
        </XStack>
        <Progress value={reviewStats.progressPct} size="$2">
          <Progress.Indicator
            backgroundColor={reviewStats.allReviewed ? '$green10' : '$blue10'}
          />
        </Progress>
      </YStack>

      {/* ── Main split layout ────────────────────────────────────────── */}
      <XStack flex={1} height="100%">
        {/* Left: Document preview with bounding-box overlays */}
        <YStack
          flex={1}
          backgroundColor="$backgroundHover"
          borderWidth={1}
          borderColor="$borderColor"
          margin="$2"
          borderRadius="$4"
          padding="$3"
          gap="$2"
        >
          <Text color="$color12" fontSize="$3" fontWeight="600">Original Document</Text>

          <DocumentPreview
            totalPages={totalPages}
            fields={bboxFields}
            highlightedFieldId={highlightedFieldId}
            onFieldClick={handleBboxFieldClick}
            currentPage={currentPage}
            onPageChange={setCurrentPage}
          />
        </YStack>

        {/* Right: Field cards */}
        <YStack flex={1} margin="$2" borderRadius="$4" gap="$2">
          <Text color="$color12" fontSize="$3" fontWeight="600" paddingHorizontal="$2">
            Extracted Fields ({fields.length})
          </Text>

          {fields.length === 0 ? (
            <YStack alignItems="center" paddingVertical="$8">
              <Text color="$color10" fontSize="$4">No extracted fields found.</Text>
              <Text color="$color10" fontSize="$2" marginTop="$2">
                The extraction job may still be processing.
              </Text>
              <Button size="$2" chromeless marginTop="$3" onPress={fetchJob}>
                Refresh
              </Button>
            </YStack>
          ) : (
            <ScrollView flex={1}>
              <YStack gap="$3" padding="$2">
                {/* Needs review fields first */}
                {fields
                  .filter((f) => f.status === 'needs_review')
                  .map((field) => (
                    <YStack
                      key={field.field_id}
                      id={`field-card-${field.field_id}`}
                      onMouseEnter={() => handleFieldHighlight(field.field_id)}
                      onMouseLeave={() => handleFieldHighlight(null)}
                    >
                      <FieldCard
                        field={field}
                        consentToken={consentToken}
                        onFieldUpdated={handleFieldUpdated}
                        onSourcePageClick={handleSourcePageClick}
                      />
                    </YStack>
                  ))}

                <Separator />

                {/* Legacy auto-approved fields are blocked pending quarantine/reprocessing. */}
                {fields.filter((f) => f.status === 'auto_approved').length > 0 && (
                  <YStack gap="$2">
                    <Text color="$color10" fontSize="$2" textTransform="uppercase">
                      Legacy auto-approved — blocked ({reviewStats.autoApproved})
                    </Text>
                    {fields
                      .filter((f) => f.status === 'auto_approved')
                      .map((field) => (
                        <YStack
                          key={field.field_id}
                          id={`field-card-${field.field_id}`}
                          onMouseEnter={() => handleFieldHighlight(field.field_id)}
                          onMouseLeave={() => handleFieldHighlight(null)}
                        >
                          <FieldCard
                            field={field}
                            consentToken={consentToken}
                            onFieldUpdated={handleFieldUpdated}
                            onSourcePageClick={handleSourcePageClick}
                          />
                        </YStack>
                      ))}
                  </YStack>
                )}

                {/* Already adjudicated fields */}
                {reviewStats.adjudicated > 0 && (
                  <YStack gap="$2">
                    <Text color="$color10" fontSize="$2" textTransform="uppercase">
                      Reviewed ({reviewStats.adjudicated})
                    </Text>
                    {fields
                      .filter(
                        (f) =>
                          f.status === 'approved' ||
                          f.status === 'edited' ||
                          f.status === 'rejected',
                      )
                      .map((field) => (
                        <YStack
                          key={field.field_id}
                          id={`field-card-${field.field_id}`}
                          onMouseEnter={() => handleFieldHighlight(field.field_id)}
                          onMouseLeave={() => handleFieldHighlight(null)}
                        >
                          <FieldCard
                            field={field}
                            consentToken={consentToken}
                            onFieldUpdated={handleFieldUpdated}
                            onSourcePageClick={handleSourcePageClick}
                          />
                        </YStack>
                      ))}
                  </YStack>
                )}
              </YStack>
            </ScrollView>
          )}
        </YStack>
      </XStack>

      {/* ── Footer bar ───────────────────────────────────────────────── */}
      <XStack
        padding="$3"
        backgroundColor="$backgroundHover"
        alignItems="center"
        justifyContent="space-between"
        borderTopWidth={1}
        borderTopColor="$borderColor"
      >
        <Button
          chromeless
          onPress={() => router.push('/doctor/pipeline/review-queue')}
        >
          ← Back to Queue
        </Button>

        <XStack alignItems="center" gap="$3">
          <Text color="$color10" fontSize="$3">
            Progress: {reviewStats.adjudicated}/{reviewStats.needsReview + reviewStats.adjudicated} reviewed
          </Text>

          {reviewStats.allReviewed && reviewStats.needsReview + reviewStats.adjudicated > 0 ? (
            <Button
              theme="green"
              size="$3"
              onPress={() =>
                router.push(
                  `/doctor/pipeline/commit/${jobId}?patient_id=${patientId}&consent_token=${consentToken}`,
                )
              }
            >
              Commit →
            </Button>
          ) : (
            <Button
              size="$3"
              disabled
            >
              {reviewStats.needsReview} field{reviewStats.needsReview !== 1 ? 's' : ''} remaining
            </Button>
          )}
        </XStack>
      </XStack>
    </YStack>
  )
}
