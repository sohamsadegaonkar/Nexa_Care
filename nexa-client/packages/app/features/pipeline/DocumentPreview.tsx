/**
 * DocumentPreview — renders an uploaded document page with bounding-box
 * overlays for AI-extracted fields.
 *
 * The preview renders a page image as a canvas background and draws
 * semi-transparent rectangles over each extracted field's source bounding
 * box. When a field is hovered or selected, its bbox highlight becomes
 * prominent.
 *
 * Bounding box format (from backend ExtractedFieldRecord):
 *   source_bbox: [x, y, width, height] — normalized 0–1 coordinates
 *   source_page: 1-based page number
 *
 * ALPHA: This is an alpha implementation. PDF.js rasterisation is not yet
 * integrated. The preview currently renders a placeholder page surface
 * with bounding-box overlays. When S3 presigned URLs or a document
 * rendering service is available, the page image will be fetched and
 * displayed as the canvas background.
 *
 * SECURITY:
 * - No hardcoded URLs or IDs.
 * - Document URLs come from the API response via shared NexaApiClient.
 * - Uses only Tamagui components.
 */

'use client'

import {
  YStack, XStack, Text, Button, Card, ScrollView,
} from '@my/ui'
import { useMemo } from 'react'

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

const RISK_OVERLAY: Record<string, { fill: string; stroke: string; label: string }> = {
  LOW_RISK: { fill: 'rgba(34,197,94,0.15)', stroke: '#22c55e', label: '✓' },
  MEDIUM_RISK: { fill: 'rgba(249,115,22,0.15)', stroke: '#f97316', label: '⚠' },
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
  const risk = RISK_OVERLAY[field.risk_level] ?? RISK_OVERLAY.MEDIUM_RISK
  const statusBorder = STATUS_BORDER[field.status] ?? '#94a3b8'

  return (
    <g
      onClick={onClick}
      style={{ cursor: 'pointer' }}
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
        <Text color="$color10" fontSize="$2" fontWeight="700">
          {pageNum}
        </Text>
      </YStack>
      <Text color={hasNeedsReview ? '$orange10' : '$color10'} fontSize="$1" fontWeight="600">
        {fieldCount} field{fieldCount !== 1 ? 's' : ''}
      </Text>
    </Card>
  )
}

// ── DocumentPreview component ──────────────────────────────────────────

export function DocumentPreview({
  totalPages,
  fields,
  highlightedFieldId,
  onFieldClick,
  currentPage,
  onPageChange,
}: DocumentPreviewProps) {
  // ── Fields on current page ─────────────────────────────────────────
  const pageFields = useMemo(
    () => fields.filter((f) => f.source_page === currentPage),
    [fields, currentPage],
  )

  const pageHasNeedsReview = pageFields.some((f) => f.status === 'needs_review')

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
    <YStack flex={1} gap="$2">
      {/* ── Page thumbnails sidebar ─────────────────────────────────── */}
      {totalPages > 1 && (
        <ScrollView horizontal showsHorizontalScrollIndicator={false}>
          <XStack gap="$2" paddingBottom="$2">
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
        {/* SVG overlay for bounding boxes */}
        <svg
          viewBox="0 0 100 100"
          preserveAspectRatio="none"
          style={{
            position: 'absolute',
            top: 0,
            left: 0,
            width: '100%',
            height: '100%',
            pointerEvents: 'none',
          }}
        >
          {/* Page content placeholder background */}
          <rect x="0" y="0" width="100" height="100" fill="#fafafa" />

          {/* Simulated document text lines for visual context */}
          {Array.from({ length: 20 }, (_, i) => (
            <rect
              key={`line-${i}`}
              x="8"
              y={5 + i * 4.5}
              width={i % 5 === 0 ? 84 : 76}
              height="2"
              rx="0.5"
              fill="#e2e8f0"
            />
          ))}

          {/* Field bounding box overlays — these ARE interactive */}
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

        {/* Page label overlay */}
        <YStack
          position="absolute"
          bottom="$2"
          right="$2"
          backgroundColor="$background"
          borderRadius="$3"
          paddingHorizontal="$2"
          paddingVertical="$1"
          borderWidth={1}
          borderColor="$borderColor"
        >
          <Text color="$color10" fontSize="$2">
            Page {currentPage} of {totalPages}
          </Text>
        </YStack>

        {/* Field count badge */}
        <YStack
          position="absolute"
          top="$2"
          right="$2"
          backgroundColor={pageHasNeedsReview ? '$orange4' : '$green4'}
          borderRadius="$3"
          paddingHorizontal="$2"
          paddingVertical="$1"
        >
          <Text
            color={pageHasNeedsReview ? '$orange10' : '$green10'}
            fontSize="$2"
            fontWeight="600"
          >
            {pageFields.length} field{pageFields.length !== 1 ? 's' : ''} on this page
          </Text>
        </YStack>
      </YStack>

      {/* ── Legend ───────────────────────────────────────────────────── */}
      {legendItems.length > 0 && (
        <XStack gap="$3" flexWrap="wrap" paddingHorizontal="$1">
          {legendItems.map((item) => (
            <XStack key={item.status} alignItems="center" gap="$1">
              <YStack
                width={12}
                height={12}
                borderRadius="$1"
                borderWidth={1}
                borderColor={item.color as any}
                borderStyle={item.dash as any}
                backgroundColor={(item.color + '22') as any}
              />
              <Text color="$color10" fontSize="$1" textTransform="uppercase">
                {item.status}
              </Text>
            </XStack>
          ))}
        </XStack>
      )}

      {/* ── Page navigation ─────────────────────────────────────────── */}
      <XStack justifyContent="center" alignItems="center" gap="$3">
        <Button
          size="$2"
          disabled={currentPage <= 1}
          onPress={() => onPageChange(Math.max(1, currentPage - 1))}
        >
          ◀ Prev
        </Button>
        <Text color="$color10" fontSize="$3">
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
