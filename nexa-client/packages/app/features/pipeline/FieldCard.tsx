/**
 * FieldCard component — displays a single extracted field for human review.
 *
 * Shows field name, extracted value, confidence badge, risk badge,
 * validation messages, source page, and Approve/Edit/Reject action buttons.
 * After a successful action, shows a brief animated confirmation before
 * transitioning to read-only mode.
 *
 * ALPHA: This is an alpha implementation. Inline editing mode and source
 * bounding-box highlighting are stubbed for future integration.
 *
 * Aligns with the canonical ExtractedField schema (WS1).
 *
 * SECURITY:
 * - No hardcoded IDs or tokens.
 * - Approve/Edit/Reject actions call NexaApiClient — never raw fetch/axios.
 * - Consent token passed as X-Consent-Token header.
 */

'use client'

import { YStack, XStack, Text, Button, Card, Input, Separator } from '@my/ui'
import { useState, useCallback, useEffect, useRef } from 'react'
import {
  NexaApiClient,
  type ExtractedField,
  type FieldReviewResponse,
  ApiError,
} from '../../utils/apiClient'

// ── Types ──────────────────────────────────────────────────────────────

export interface FieldCardProps {
  field: ExtractedField
  consentToken: string
  onFieldUpdated?: (fieldId: string, newStatus: string, finalValue: string) => void
  onSourcePageClick?: (page: number) => void
}

// ── Risk level styling ─────────────────────────────────────────────────

type RiskLevel = ExtractedField['risk_level']

const RISK_STYLES = {
  LOW_RISK: { bg: '$green4', text: '$green10', icon: '✓' },
  MEDIUM_RISK: { bg: '$orange4', text: '$orange10', icon: '⚠' },
  HIGH_RISK: { bg: '$red4', text: '$red10', icon: '⛔' },
  CRITICAL_RISK: { bg: '$red4', text: '$red10', icon: '🚨' },
} as const satisfies Record<RiskLevel, { bg: string; text: string; icon: string }>

// ── ProvenanceBadge ────────────────────────────────────────────────────

/**
 * ProvenanceBadge — shows verification status of an extracted field.
 *
 * - AI confidence ≥ 0.9 + approved → "Clinician verified" (green)
 * - AI confidence ≥ 0.9 + not yet verified → "AI extracted · X% · Not yet verified" (yellow)
 * - AI confidence < 0.9 → "AI extracted · X% · Not yet verified" (orange/red)
 * - Legacy auto-approved → blocked, never treated as clinician review
 */
function ProvenanceBadge({ confidence, status }: { confidence: number; status: string }) {
  const pct = Math.round(confidence * 100)

  if (status === 'approved' || status === 'edited') {
    return (
      <Card
        backgroundColor="$green4"
        borderRadius="$4"
        paddingHorizontal="$2"
        paddingVertical="$1"
      >
        <Text
          color="$green10"
          fontSize="$1"
          fontWeight="600"
        >
          Clinician verified
        </Text>
      </Card>
    )
  }

  if (status === 'auto_approved') {
    return (
      <Card
        backgroundColor="$red4"
        borderRadius="$4"
        paddingHorizontal="$2"
        paddingVertical="$1"
      >
        <Text
          color="$red10"
          fontSize="$1"
          fontWeight="600"
        >
          Legacy auto-approved blocked · {pct}% model confidence
        </Text>
      </Card>
    )
  }

  const badgeBg = confidence >= 0.9 ? '$yellow4' : '$orange4'
  const badgeText = confidence >= 0.9 ? '$yellow10' : '$orange10'

  return (
    <Card
      backgroundColor={badgeBg}
      borderRadius="$4"
      paddingHorizontal="$2"
      paddingVertical="$1"
    >
      <Text
        color={badgeText}
        fontSize="$1"
        fontWeight="600"
      >
        AI extracted · {pct}% model confidence · Not yet verified
      </Text>
    </Card>
  )
}

// ── ActionConfirmation — brief animated feedback after action ───────────

/**
 * ActionConfirmation — shows a brief success confirmation after an
 * adjudication action (approve, edit, reject). Automatically fades
 * after ~1.5 seconds.
 */
function ActionConfirmation({ action }: { action: 'approved' | 'edited' | 'rejected' }) {
  const config = (
    {
      approved: { icon: '✓', label: 'Approved', bg: '$green4', text: '$green10' },
      edited: { icon: '✎', label: 'Edited', bg: '$yellow4', text: '$yellow10' },
      rejected: { icon: '✕', label: 'Rejected', bg: '$red4', text: '$red10' },
    } as const
  )[action]

  return (
    <Card
      backgroundColor={config.bg}
      borderRadius="$4"
      padding="$3"
      alignItems="center"
      gap="$1"
    >
      <Text
        color={config.text}
        fontSize="$6"
        fontWeight="700"
      >
        {config.icon}
      </Text>
      <Text
        color={config.text}
        fontSize="$3"
        fontWeight="600"
      >
        {config.label}
      </Text>
    </Card>
  )
}

// ── FieldCard component ────────────────────────────────────────────────

export function FieldCard({
  field,
  consentToken,
  onFieldUpdated,
  onSourcePageClick,
}: FieldCardProps) {
  const [actionLoading, setActionLoading] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [editMode, setEditMode] = useState(false)
  const [editValue, setEditValue] = useState(field.corrected_value ?? field.raw_value)
  const [rejectMode, setRejectMode] = useState(false)
  const [rejectNotes, setRejectNotes] = useState('')
  const [lastAction, setLastAction] = useState<'approved' | 'edited' | 'rejected' | null>(null)
  const confirmTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const isAdjudicated =
    field.status === 'approved' || field.status === 'edited' || field.status === 'rejected'
  const isAutoApproved = field.status === 'auto_approved'
  /** Read-only fields: auto-approved (already passed) or adjudicated. */
  const isReadOnly = isAdjudicated || isAutoApproved

  const risk = RISK_STYLES[field.risk_level] ?? RISK_STYLES.MEDIUM_RISK

  // ── Clean up confirmation timer on unmount ─────────────────────────
  useEffect(() => {
    return () => {
      if (confirmTimerRef.current) {
        clearTimeout(confirmTimerRef.current)
      }
    }
  }, [])

  // ── Show action confirmation briefly ───────────────────────────────
  const showConfirmation = useCallback((action: 'approved' | 'edited' | 'rejected') => {
    setLastAction(action)
    if (confirmTimerRef.current) clearTimeout(confirmTimerRef.current)
    confirmTimerRef.current = setTimeout(() => {
      setLastAction(null)
    }, 1500)
  }, [])

  // ── Adjudication actions ───────────────────────────────────────────
  const handleApprove = useCallback(async () => {
    setActionLoading(true)
    setActionError(null)
    try {
      const data = await NexaApiClient.reviewField(
        field.field_id,
        { action: 'approve' },
        consentToken
      )
      showConfirmation('approved')
      onFieldUpdated?.(data.field_id, data.new_status, data.final_value)
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setActionError('Session expired.')
      } else if (err instanceof ApiError && err.status === 403) {
        setActionError('Consent required for field adjudication.')
      } else {
        setActionError('Failed to approve field. Please try again.')
      }
    } finally {
      setActionLoading(false)
    }
  }, [field.field_id, consentToken, onFieldUpdated, showConfirmation])

  const handleEdit = useCallback(async () => {
    if (!editValue.trim()) return
    setActionLoading(true)
    setActionError(null)
    try {
      const data = await NexaApiClient.reviewField(
        field.field_id,
        { action: 'edit', corrected_value: editValue },
        consentToken
      )
      setEditMode(false)
      showConfirmation('edited')
      onFieldUpdated?.(data.field_id, data.new_status, data.final_value)
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setActionError('Session expired.')
      } else if (err instanceof ApiError && err.status === 403) {
        setActionError('Consent required for field adjudication.')
      } else {
        setActionError('Failed to edit field. Please try again.')
      }
    } finally {
      setActionLoading(false)
    }
  }, [field.field_id, consentToken, editValue, onFieldUpdated, showConfirmation])

  const handleReject = useCallback(async () => {
    setActionLoading(true)
    setActionError(null)
    try {
      const data = await NexaApiClient.reviewField(
        field.field_id,
        { action: 'reject', review_notes: rejectNotes || undefined },
        consentToken
      )
      setRejectMode(false)
      showConfirmation('rejected')
      onFieldUpdated?.(data.field_id, data.new_status, data.final_value)
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setActionError('Session expired.')
      } else if (err instanceof ApiError && err.status === 403) {
        setActionError('Consent required for field adjudication.')
      } else {
        setActionError('Failed to reject field. Please try again.')
      }
    } finally {
      setActionLoading(false)
    }
  }, [field.field_id, consentToken, rejectNotes, onFieldUpdated, showConfirmation])

  // ── Render ─────────────────────────────────────────────────────────

  // Show brief confirmation overlay after successful action
  if (lastAction && isAdjudicated) {
    return (
      <Card
        padding="$4"
        backgroundColor="$background"
        borderRadius="$4"
        borderWidth={1}
        borderColor="$borderColor"
        gap="$2"
        opacity={0.9}
      >
        <ActionConfirmation action={lastAction} />
        <XStack
          justifyContent="space-between"
          alignItems="center"
        >
          <Text
            color="$color10"
            fontSize="$2"
          >
            {field.field_name}
          </Text>
          <Text
            color="$color10"
            fontSize="$2"
          >
            {field.corrected_value ?? field.raw_value}
          </Text>
        </XStack>
      </Card>
    )
  }

  return (
    <Card
      padding="$4"
      backgroundColor={isReadOnly ? '$backgroundHover' : '$background'}
      borderRadius="$4"
      borderWidth={1}
      borderColor={isAdjudicated ? '$borderColor' : isAutoApproved ? '$blue5' : '$orange5'}
      gap="$2"
      opacity={field.status === 'rejected' ? 0.6 : 1}
    >
      {/* Header: field name + status */}
      <XStack
        justifyContent="space-between"
        alignItems="center"
      >
        <Text
          color="$color12"
          fontSize="$4"
          fontWeight="700"
        >
          {field.field_name}
        </Text>
        <Card
          backgroundColor={
            field.status === 'needs_review'
              ? '$orange4'
              : field.status === 'approved' || field.status === 'edited'
                ? '$green4'
                : field.status === 'rejected'
                  ? '$red4'
                  : '$blue4'
          }
          borderRadius="$4"
          paddingHorizontal="$2"
          paddingVertical="$1"
        >
          <Text
            color={
              field.status === 'needs_review'
                ? '$orange10'
                : field.status === 'approved' || field.status === 'edited'
                  ? '$green10'
                  : field.status === 'rejected'
                    ? '$red10'
                    : '$blue10'
            }
            fontSize="$1"
            fontWeight="600"
            textTransform="uppercase"
          >
            {field.status.replace('_', ' ')}
          </Text>
        </Card>
      </XStack>

      {/* Extracted value */}
      <YStack gap="$1">
        <Text
          color="$color10"
          fontSize="$2"
          textTransform="uppercase"
        >
          Extracted Value
        </Text>
        <Text
          color="$color12"
          fontSize="$5"
          fontWeight="600"
        >
          {field.corrected_value ?? field.raw_value}
        </Text>
        {field.normalized_value && field.normalized_value !== field.raw_value && (
          <Text
            color="$color10"
            fontSize="$2"
          >
            Normalized: {field.normalized_value}
          </Text>
        )}
      </YStack>

      {/* Confidence + Risk badges */}
      <XStack
        gap="$2"
        alignItems="center"
        flexWrap="wrap"
      >
        <ProvenanceBadge
          confidence={field.confidence}
          status={field.status}
        />
        <Card
          backgroundColor={risk.bg}
          borderRadius="$4"
          paddingHorizontal="$2"
          paddingVertical="$1"
        >
          <Text
            color={risk.text}
            fontSize="$1"
            fontWeight="600"
          >
            {risk.icon} {field.risk_level.replace('_', ' ')}
          </Text>
        </Card>
      </XStack>

      {/* Validation messages */}
      {field.validation_result && !field.validation_result.is_valid && (
        <YStack
          backgroundColor="$red3"
          borderRadius="$3"
          padding="$2"
          gap="$1"
        >
          <Text
            color="$red10"
            fontSize="$2"
            fontWeight="600"
          >
            Validation Issues:
          </Text>
          {field.validation_result.validation_errors.map((msg, idx) => (
            <Text
              key={idx}
              color="$red10"
              fontSize="$2"
            >
              • {msg}
            </Text>
          ))}
        </YStack>
      )}

      {/* Reference range */}
      {field.validation_result?.reference_range && (
        <Text
          color="$color10"
          fontSize="$2"
        >
          Reference: {field.validation_result.reference_range.min}–
          {field.validation_result.reference_range.max}{' '}
          {field.validation_result.reference_range.unit}
        </Text>
      )}

      {/* Source page */}
      <XStack
        alignItems="center"
        gap="$2"
      >
        <Text
          color="$color10"
          fontSize="$2"
        >
          Source: Page {field.source_page}
        </Text>
        {onSourcePageClick && (
          <Button
            size="$1"
            chromeless
            onPress={() => onSourcePageClick(field.source_page)}
          >
            Jump
          </Button>
        )}
      </XStack>

      {/* Edit mode — shows original value for comparison */}
      {editMode && !isReadOnly && (
        <YStack
          gap="$2"
          backgroundColor="$blue2"
          borderRadius="$3"
          padding="$3"
        >
          <Text
            color="$blue10"
            fontSize="$2"
            fontWeight="600"
          >
            Editing Field
          </Text>
          {/* Original value for reference */}
          <YStack gap="$1">
            <Text
              color="$color10"
              fontSize="$2"
            >
              Original AI extraction:
            </Text>
            <Text
              color="$color10"
              fontSize="$3"
              textDecorationLine="line-through"
            >
              {field.raw_value}
            </Text>
          </YStack>
          <Separator />
          {/* Corrected value input */}
          <YStack gap="$1">
            <Text
              color="$blue10"
              fontSize="$2"
              fontWeight="600"
            >
              Corrected value:
            </Text>
            <Input
              value={editValue}
              onChangeText={setEditValue}
              placeholder="Enter corrected value…"
              size="$3"
              autoFocus
            />
          </YStack>
          <XStack gap="$2">
            <Button
              theme="blue"
              size="$2"
              disabled={actionLoading || !editValue.trim()}
              onPress={handleEdit}
            >
              {actionLoading ? 'Saving…' : 'Save Edit'}
            </Button>
            <Button
              size="$2"
              chromeless
              onPress={() => {
                setEditMode(false)
                setEditValue(field.corrected_value ?? field.raw_value)
              }}
            >
              Cancel
            </Button>
          </XStack>
        </YStack>
      )}

      {/* Reject mode */}
      {rejectMode && !isReadOnly && (
        <YStack
          gap="$2"
          backgroundColor="$red2"
          borderRadius="$3"
          padding="$3"
        >
          <Text
            color="$red10"
            fontSize="$2"
            fontWeight="600"
          >
            Rejecting Field
          </Text>
          <YStack gap="$1">
            <Text
              color="$color10"
              fontSize="$2"
            >
              Value to exclude:
            </Text>
            <Text
              color="$color10"
              fontSize="$3"
              textDecorationLine="line-through"
            >
              {field.raw_value}
            </Text>
          </YStack>
          <Separator />
          <Input
            value={rejectNotes}
            onChangeText={setRejectNotes}
            placeholder="Rejection reason (optional)…"
            size="$3"
          />
          <XStack gap="$2">
            <Button
              theme="red"
              size="$2"
              disabled={actionLoading}
              onPress={handleReject}
            >
              {actionLoading ? 'Rejecting…' : 'Confirm Reject'}
            </Button>
            <Button
              size="$2"
              chromeless
              onPress={() => {
                setRejectMode(false)
                setRejectNotes('')
              }}
            >
              Cancel
            </Button>
          </XStack>
        </YStack>
      )}

      {/* Action buttons (only for needs_review) */}
      {!isReadOnly && !editMode && !rejectMode && (
        <XStack
          gap="$2"
          marginTop="$1"
        >
          <Button
            theme="green"
            size="$2"
            disabled={actionLoading}
            onPress={handleApprove}
          >
            {actionLoading ? 'Approving…' : 'Approve'}
          </Button>
          <Button
            theme="blue"
            size="$2"
            disabled={actionLoading}
            onPress={() => setEditMode(true)}
          >
            Edit
          </Button>
          <Button
            theme="red"
            size="$2"
            disabled={actionLoading}
            onPress={() => setRejectMode(true)}
          >
            Reject
          </Button>
        </XStack>
      )}

      {/* Action error */}
      {actionError && (
        <Card
          backgroundColor="$red4"
          borderRadius="$3"
          padding="$2"
          gap="$1"
        >
          <Text
            color="$red10"
            fontSize="$2"
          >
            {actionError}
          </Text>
          <Button
            size="$1"
            chromeless
            onPress={() => setActionError(null)}
          >
            Dismiss
          </Button>
        </Card>
      )}

      {/* Adjudication info for completed fields */}
      {isReadOnly && !lastAction && (
        <Text
          color="$color10"
          fontSize="$2"
        >
          {isAutoApproved
            ? 'This field was auto-approved by the AI pipeline.'
            : `This field has been ${field.status === 'edited' ? 'corrected' : field.status}.`}
          {field.corrected_value ? ` Final value: ${field.corrected_value}` : ''}
        </Text>
      )}
    </Card>
  )
}
