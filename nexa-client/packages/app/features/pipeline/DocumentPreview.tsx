/**
 * DocumentPreview — renders an uploaded document page with bounding-box
 * overlays for AI-extracted fields.
 *
 * The preview loads the authorized original document on explicit request.
 * For image sources it can also draw source bounding boxes over page one.
 *
 * Bounding box format (from backend ExtractedFieldRecord):
 *   source_bbox: [x, y, width, height] — normalized 0–1 coordinates
 *   source_page: 1-based page number
 *
 * SECURITY:
 * - Source bytes are requested through the consent-gated, audited API.
 * - Bearer capabilities are sent in the request header and never in URLs.
 * - Temporary object URLs are revoked when the source changes or unmounts.
 */

'use client'

import { YStack, XStack, Text, Button, Card, ScrollView, Paragraph, Spinner } from '@my/ui'
import { useEffect, useMemo, useRef, useState } from 'react'
import { ApiError, NexaApiClient } from '../../utils/apiClient'

// ── Types ──────────────────────────────────────────────────────────────

export interface BBoxField {
  field_id: string
  field_name: string
  source_page: number
  source_bbox: [number, number, number, number] | null
  status: string
  risk_level: string
}

export interface DocumentPreviewProps {
  /** Opaque server job identifier for the authorized source-view request. */
  jobId: string
  /** Memory-only consent capability; sent only in the request header. */
  consentToken: string
  /** Total number of pages in the document. */
  totalPages: number
  /** All extracted fields with bounding box data. */
  fields: BBoxField[]
  /** Currently highlighted field ID (from hover/select on FieldCard). */
  highlightedFieldId: string | null
  /** Callback when a bbox region is clicked. */
  onFieldClick?: (fieldId: string) => void
  /** Current page (1-based). */
  currentPage: number
  /** Callback to change page. */
  onPageChange: (page: number) => void
}

// ── Risk level → overlay colour ────────────────────────────────────────

type RiskOverlay = { fill: string; stroke: string; label: string }

const DEFAULT_RISK_OVERLAY: RiskOverlay = {
  fill: 'rgba(249,115,22,0.15)',
  stroke: '#f97316',
  label: '⚠',
}

const RISK_OVERLAY: Record<string, RiskOverlay> = {
  LOW_RISK: { fill: 'rgba(34,197,94,0.15)', stroke: '#22c55e', label: '✓' },
  MEDIUM_RISK: DEFAULT_RISK_OVERLAY,
  HIGH_RISK: { fill: 'rgba(239,68,68,0.15)', stroke: '#ef4444', label: '⛔' },
  CRITICAL_RISK: { fill: 'rgba(239,68,68,0.25)', stroke: '#dc2626', label: '🚨' },
}

const STATUS_BORDER: Record<string, string> = {
  auto_approved: '#3b82f6',
  needs_review: '#f97316',
  approved: '#22c55e',
  edited: '#8b5cf6',
  rejected: '#ef4444',
}

// ── BBoxOverlay — SVG rect for a single field ──────────────────────────

function BBoxOverlay({
  field,
  isHighlighted,
  onClick,
}: {
  field: BBoxField
  isHighlighted: boolean
  onClick: () => void
}) {
  if (!field.source_bbox) return null

  const [x, y, w, h] = field.source_bbox
  const risk = RISK_OVERLAY[field.risk_level] ?? DEFAULT_RISK_OVERLAY
  const statusBorder = STATUS_BORDER[field.status] ?? '#94a3b8'

  return (
    <g
      aria-label={`Select ${field.field_name}`}
      onClick={onClick}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          onClick()
        }
      }}
      // biome-ignore lint/a11y/useSemanticElements: SVG groups have no native button equivalent.
      role="button"
      style={{ cursor: 'pointer' }}
      tabIndex={0}
    >
      {/* Fill rectangle */}
      <rect
        x={`${x * 100}%`}
        y={`${y * 100}%`}
        width={`${w * 100}%`}
        height={`${h * 100}%`}
        fill={isHighlighted ? risk.fill.replace('0.15', '0.35').replace('0.25', '0.45') : risk.fill}
        stroke={isHighlighted ? statusBorder : risk.stroke}
        strokeWidth={isHighlighted ? 2.5 : 1}
        strokeDasharray={field.status === 'needs_review' ? '4 2' : undefined}
        rx={2}
      />
      {/* Label */}
      <text
        x={`${(x + w / 2) * 100}%`}
        y={`${y * 100 - 0.5}%`}
        textAnchor="middle"
        fontSize={isHighlighted ? 11 : 9}
        fontWeight={isHighlighted ? 700 : 500}
        fill={isHighlighted ? statusBorder : risk.stroke}
        style={{ pointerEvents: 'none' }}
      >
        {risk.label} {field.field_name}
      </text>
    </g>
  )
}

// ── PageThumbnail — mini thumbnail for page nav ────────────────────────

function PageThumbnail({
  pageNum,
  fieldCount,
  isActive,
  hasNeedsReview,
  onPress,
}: {
  pageNum: number
  fieldCount: number
  isActive: boolean
  hasNeedsReview: boolean
  onPress: () => void
}) {
  return (
    <Card
      backgroundColor={isActive ? '$backgroundFocus' : '$backgroundHover'}
      borderWidth={isActive ? 2 : 1}
      borderColor={isActive ? '$blue8' : hasNeedsReview ? '$orange8' : '$borderColor'}
      borderRadius="$3"
      padding="$2"
      alignItems="center"
      gap="$1"
      hoverStyle={{ backgroundColor: '$backgroundFocus' }}
      pressStyle={{ backgroundColor: '$backgroundPress' }}
      onPress={onPress}
      role="button"
      aria-label={`Show page ${pageNum}`}
      aria-pressed={isActive}
      tabIndex={0}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          onPress()
        }
      }}
    >
      <YStack
        width={36}
        height={48}
        backgroundColor="$background"
        borderRadius="$2"
        justifyContent="center"
        alignItems="center"
        borderWidth={1}
        borderColor="$borderColor"
      >
        <Text
          color="$color10"
          fontSize="$2"
          fontWeight="700"
        >
          {pageNum}
        </Text>
      </YStack>
      <Text
        color={hasNeedsReview ? '$orange10' : '$color10'}
        fontSize="$1"
        fontWeight="600"
      >
        {fieldCount} field{fieldCount !== 1 ? 's' : ''}
      </Text>
    </Card>
  )
}

// ── DocumentPreview component ──────────────────────────────────────────

export function DocumentPreview({
  jobId,
  consentToken,
  totalPages,
  fields,
  highlightedFieldId,
  onFieldClick,
  currentPage,
  onPageChange,
}: DocumentPreviewProps) {
  const [source, setSource] = useState<{
    url: string
    contentType: string
    generation: number
  } | null>(null)
  const [sourceLoading, setSourceLoading] = useState(false)
  const [sourceError, setSourceError] = useState<string | null>(null)
  const sourceRequestRef = useRef(0)
  const sourceContextRef = useRef({ jobId, consentToken, generation: 0 })

  if (
    sourceContextRef.current.jobId !== jobId ||
    sourceContextRef.current.consentToken !== consentToken
  ) {
    sourceRequestRef.current += 1
    sourceContextRef.current = {
      jobId,
      consentToken,
      generation: sourceContextRef.current.generation + 1,
    }
  }
  const sourceGeneration = sourceContextRef.current.generation
  const visibleSource = source?.generation === sourceGeneration ? source : null

  useEffect(() => {
    const objectUrl = source?.url
    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [source?.url])

  useEffect(
    () => () => {
      sourceRequestRef.current += 1
    },
    []
  )

  useEffect(() => {
    setSource(null)
    setSourceError(null)
    setSourceLoading(false)
  }, [sourceGeneration])

  const loadSource = async () => {
    if (!jobId || !consentToken || sourceLoading) return
    const requestId = ++sourceRequestRef.current
    const generation = sourceGeneration
    setSourceLoading(true)
    setSourceError(null)
    try {
      const blob = await NexaApiClient.getExtractionJobDocument(jobId, consentToken)
      if (
        requestId !== sourceRequestRef.current ||
        generation !== sourceContextRef.current.generation
      ) return
      setSource({
        url: URL.createObjectURL(blob),
        contentType: blob.type.toLowerCase(),
        generation,
      })
    } catch (error) {
      if (
        requestId !== sourceRequestRef.current ||
        generation !== sourceContextRef.current.generation
      ) return
      if (error instanceof ApiError && error.status === 403) {
        setSourceError(
          'Your current consent does not permit viewing this source. Request the required access before continuing.'
        )
      } else if (error instanceof ApiError && error.status === 404) {
        setSourceError('The original source document is unavailable.')
      } else {
        setSourceError('The original source could not be loaded. Retry or return to the review queue.')
      }
    } finally {
      if (
        requestId === sourceRequestRef.current &&
        generation === sourceContextRef.current.generation
      ) setSourceLoading(false)
    }
  }

  // ── Fields on current page ─────────────────────────────────────────
  const pageFields = useMemo(
    () => fields.filter((f) => f.source_page === currentPage),
    [fields, currentPage]
  )

  // ── Page thumbnail data ────────────────────────────────────────────
  const pageThumbnails = useMemo(() => {
    const thumbs: { page: number; count: number; hasNeedsReview: boolean }[] = []
    for (let p = 1; p <= totalPages; p++) {
      const pf = fields.filter((f) => f.source_page === p)
      thumbs.push({
        page: p,
        count: pf.length,
        hasNeedsReview: pf.some((f) => f.status === 'needs_review'),
      })
    }
    return thumbs
  }, [fields, totalPages])

  // ── Legend ─────────────────────────────────────────────────────────
  const legendItems = useMemo(() => {
    const seen = new Set<string>()
    const items: { status: string; color: string; dash: string }[] = []
    for (const f of pageFields) {
      if (!seen.has(f.status)) {
        seen.add(f.status)
        items.push({
          status: f.status.replace('_', ' '),
          color: STATUS_BORDER[f.status] ?? '#94a3b8',
          dash: f.status === 'needs_review' ? 'dashed' : 'solid',
        })
      }
    }
    return items
  }, [pageFields])

  return (
    <YStack
      flex={1}
      gap="$2"
    >
      {/* ── Page thumbnails sidebar ─────────────────────────────────── */}
      {totalPages > 1 && (
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
        >
          <XStack
            gap="$2"
            paddingBottom="$2"
          >
            {pageThumbnails.map((t) => (
              <PageThumbnail
                key={t.page}
                pageNum={t.page}
                fieldCount={t.count}
                isActive={t.page === currentPage}
                hasNeedsReview={t.hasNeedsReview}
                onPress={() => onPageChange(t.page)}
              />
            ))}
          </XStack>
        </ScrollView>
      )}

      {/* ── Document page canvas with bbox overlays ─────────────────── */}
      <YStack
        flex={1}
        backgroundColor="$background"
        borderRadius="$4"
        borderWidth={1}
        borderColor="$borderColor"
        overflow="hidden"
        position="relative"
        minHeight={400}
      >
        {!visibleSource && !sourceLoading && !sourceError && (
          <YStack
            flex={1}
            minHeight={400}
            justifyContent="center"
            alignItems="center"
            gap="$3"
            padding="$4"
          >
            <Paragraph textAlign="center">
              No document page is shown until the original source is loaded. Extracted field details remain available beside this panel.
            </Paragraph>
            <Button
              theme="blue"
              minHeight={48}
              disabled={!jobId || !consentToken}
              aria-disabled={!jobId || !consentToken}
              onPress={() => void loadSource()}
              aria-label="Load original source document"
            >
              Load original source
            </Button>
          </YStack>
        )}

        {sourceLoading && (
          <YStack
            flex={1}
            minHeight={400}
            justifyContent="center"
            alignItems="center"
            gap="$3"
          >
            <Spinner size="large" />
            <Paragraph>Checking access and loading the original source.</Paragraph>
          </YStack>
        )}

        {sourceError && (
          <YStack
            flex={1}
            minHeight={400}
            justifyContent="center"
            alignItems="center"
            gap="$3"
            padding="$4"
            role="alert"
          >
            <Paragraph color="$red10" textAlign="center">{sourceError}</Paragraph>
            <Button
              onPress={() => void loadSource()}
              disabled={sourceLoading || !jobId || !consentToken}
              aria-disabled={sourceLoading || !jobId || !consentToken}
              aria-label="Retry loading original source document"
            >
              Retry source view
            </Button>
          </YStack>
        )}

        {visibleSource?.contentType === 'application/pdf' && (
          <>
            <object
              aria-label="Original source document"
              data={visibleSource.url + '#page=' + currentPage}
              type="application/pdf"
              style={{ display: 'block', width: '100%', minHeight: 640, border: 0 }}
            >
              <Paragraph padding="$4">
                This browser could not display the protected PDF. No external viewer was opened.
              </Paragraph>
            </object>
            {pageFields.some((field) => field.source_bbox) && (
              <Paragraph color="$color10" size="$2" padding="$3">
                Bounding boxes are not overlaid on the browser PDF viewer. Use the field details and page controls to compare against the source.
              </Paragraph>
            )}
          </>
        )}

        {visibleSource?.contentType.startsWith('image/') && currentPage === 1 && (
          <div style={{ position: 'relative', width: '100%' }}>
            <img
              src={visibleSource.url}
              alt="Original source document page"
              style={{ display: 'block', width: '100%', height: 'auto' }}
            />
            <svg
              viewBox="0 0 100 100"
              preserveAspectRatio="none"
              aria-label="Source field locations"
              style={{
                position: 'absolute',
                inset: 0,
                width: '100%',
                height: '100%',
                pointerEvents: 'none',
              }}
            >
              <g style={{ pointerEvents: 'auto' }}>
                {pageFields.map((field) => (
                  <BBoxOverlay
                    key={field.field_id}
                    field={field}
                    isHighlighted={highlightedFieldId === field.field_id}
                    onClick={() => onFieldClick?.(field.field_id)}
                  />
                ))}
              </g>
            </svg>
          </div>
        )}

        {visibleSource?.contentType.startsWith('image/') && currentPage > 1 && (
          <YStack
            flex={1}
            minHeight={400}
            justifyContent="center"
            alignItems="center"
            padding="$4"
          >
            <Paragraph textAlign="center">
              This source image contains one page. The original image for page {currentPage} is not available.
            </Paragraph>
          </YStack>
        )}

        {visibleSource &&
          visibleSource.contentType !== 'application/pdf' &&
          !visibleSource.contentType.startsWith('image/') && (
            <YStack
              flex={1}
              minHeight={400}
              justifyContent="center"
              alignItems="center"
              padding="$4"
            >
              <Paragraph textAlign="center">
                This source format cannot be displayed in the browser. No document was sent to an external viewer.
              </Paragraph>
            </YStack>
          )}
      </YStack>

      {/* ── Legend ───────────────────────────────────────────────────── */}
      {legendItems.length > 0 && (
        <XStack
          gap="$3"
          flexWrap="wrap"
          paddingHorizontal="$1"
        >
          {legendItems.map((item) => (
            <XStack
              key={item.status}
              alignItems="center"
              gap="$1"
            >
              <YStack
                width={12}
                height={12}
                borderRadius="$1"
                borderWidth={1}
                borderColor={item.color as any}
                borderStyle={item.dash as any}
                backgroundColor={(item.color + '22') as any}
              />
              <Text
                color="$color10"
                fontSize="$1"
                textTransform="uppercase"
              >
                {item.status}
              </Text>
            </XStack>
          ))}
        </XStack>
      )}

      {/* ── Page navigation ─────────────────────────────────────────── */}
      <XStack
        justifyContent="center"
        alignItems="center"
        gap="$3"
      >
        <Button
          size="$2"
          disabled={currentPage <= 1}
          onPress={() => onPageChange(Math.max(1, currentPage - 1))}
        >
          ◀ Prev
        </Button>
        <Text
          color="$color10"
          fontSize="$3"
        >
          Page {currentPage} / {totalPages}
        </Text>
        <Button
          size="$2"
          disabled={currentPage >= totalPages}
          onPress={() => onPageChange(Math.min(totalPages, currentPage + 1))}
        >
          Next ▶
        </Button>
      </XStack>
    </YStack>
  )
}
