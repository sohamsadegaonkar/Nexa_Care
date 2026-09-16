import React, { useEffect, useState } from 'react'
import {
  Button,
  Paragraph,
  Separator,
  Sheet,
  Spinner,
  Text,
  XStack,
  YStack,
} from 'tamagui'
import {
  NexaApiClient,
  type PatientRecordDetailResponse,
} from '../../utils/apiClient'
import RiskBadge, { type RiskLevel } from './badges/RiskBadge'
import SourceBadge from './badges/SourceBadge'

interface PatientRecordDetailModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  category: string
  recordId: string | null
  initialTitle?: string
  initialFields?: Record<string, any>
  initialProvenance?: {
    source: string
    source_display?: string
    confidence?: number | null
    risk_level?: string | null
    has_source_document?: boolean
  }
  initialRecordedAt?: string | null
}

function formatFieldKey(key: string): string {
  return key
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

function formatFieldValue(val: any): string {
  if (val === null || val === undefined) return 'Not recorded'
  if (typeof val === 'boolean') return val ? 'Yes' : 'No'
  if (typeof val === 'object') return JSON.stringify(val)
  return String(val)
}

export default function PatientRecordDetailModal({
  open,
  onOpenChange,
  category,
  recordId,
  initialTitle,
  initialFields,
  initialProvenance,
  initialRecordedAt,
}: PatientRecordDetailModalProps) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [detail, setDetail] = useState<PatientRecordDetailResponse | null>(null)

  useEffect(() => {
    if (!open || !recordId || !category) {
      setDetail(null)
      setError(null)
      return
    }

    let isMounted = true
    setLoading(true)
    setError(null)

    NexaApiClient.getMyRecordDetail(category, recordId)
      .then((res) => {
        if (isMounted) {
          setDetail(res)
          setLoading(false)
        }
      })
      .catch((err) => {
        if (isMounted) {
          setError(
            err instanceof Error ? err.message : 'Failed to load record details'
          )
          setLoading(false)
        }
      })

    return () => {
      isMounted = false
    }
  }, [open, category, recordId])

  const title = detail?.title || initialTitle || 'Record Details'
  const fields = detail?.fields || initialFields || {}
  const provenance = detail?.provenance || initialProvenance || {
    source: 'manual',
    source_display: 'Manual entry',
    has_source_document: false,
  }
  const recordedAt = detail?.recorded_at || initialRecordedAt

  const formattedDate = recordedAt
    ? new Date(recordedAt).toLocaleString('en-IN', {
        dateStyle: 'medium',
        timeStyle: 'short',
      })
    : null

  return (
    <Sheet
      modal
      open={open}
      onOpenChange={onOpenChange}
      snapPoints={[75, 90]}
      dismissOnSnapToBottom
    >
      <Sheet.Overlay
        backgroundColor="rgba(0, 0, 0, 0.5)"
      />
      <Sheet.Frame
        padding="$4"
        backgroundColor="$background"
        borderTopLeftRadius="$6"
        borderTopRightRadius="$6"
        gap="$3"
      >
        <Sheet.Handle backgroundColor="$color8" />

        {/* Header */}
        <XStack
          justifyContent="space-between"
          alignItems="flex-start"
          gap="$2"
        >
          <YStack flex={1} gap="$1">
            <XStack alignItems="center" gap="$2" flexWrap="wrap">
              <Text
                color="$color11"
                fontSize="$2"
                textTransform="uppercase"
                fontWeight="700"
                letterSpacing={1}
              >
                {category}
              </Text>
              {formattedDate ? (
                <Text color="$color10" fontSize="$2">
                  • {formattedDate}
                </Text>
              ) : null}
            </XStack>
            <Text
              color="$color"
              fontSize="$6"
              fontWeight="800"
            >
              {title}
            </Text>
          </YStack>
          <Button
            size="$2"
            circular
            chromeless
            onPress={() => onOpenChange(false)}
            accessibilityLabel="Close record details"
          >
            ✕
          </Button>
        </XStack>

        <Separator />

        {loading ? (
          <YStack
            flex={1}
            alignItems="center"
            justifyContent="center"
            paddingVertical="$8"
            gap="$3"
          >
            <Spinner size="large" color="$blue10" />
            <Paragraph color="$color10">Loading clinical details…</Paragraph>
          </YStack>
        ) : (
          <YStack gap="$4" overflow="hidden">
            {error ? (
              <YStack
                backgroundColor="$red4"
                padding="$3"
                borderRadius="$3"
                gap="$1"
              >
                <Text color="$red11" fontSize="$3" fontWeight="600">
                  ⚠️ Notice
                </Text>
                <Paragraph color="$red11" size="$2">
                  {error} (Showing available summary)
                </Paragraph>
              </YStack>
            ) : null}

            {/* Structured Clinical Observations */}
            <YStack
              backgroundColor="$backgroundHover"
              borderRadius="$4"
              padding="$3.5"
              gap="$2.5"
            >
              <Text
                color="$color"
                fontSize="$3"
                fontWeight="700"
                textTransform="uppercase"
                letterSpacing={0.5}
              >
                Clinical Data
              </Text>

              {Object.keys(fields).length > 0 ? (
                <YStack gap="$2">
                  {Object.entries(fields).map(([key, val]) => (
                    <XStack
                      key={key}
                      justifyContent="space-between"
                      alignItems="center"
                      paddingVertical="$1"
                      borderBottomWidth={1}
                      borderBottomColor="$borderColor"
                    >
                      <Text color="$color10" fontSize="$3" fontWeight="500">
                        {formatFieldKey(key)}
                      </Text>
                      <Text
                        color="$color"
                        fontSize="$3"
                        fontWeight="700"
                        textAlign="right"
                      >
                        {formatFieldValue(val)}
                      </Text>
                    </XStack>
                  ))}
                </YStack>
              ) : (
                <Paragraph color="$color10" size="$2">
                  No structured field values recorded.
                </Paragraph>
              )}
            </YStack>

            {/* Provenance and Verification Card */}
            <YStack
              backgroundColor="$backgroundHover"
              borderRadius="$4"
              padding="$3.5"
              gap="$2.5"
            >
              <Text
                color="$color"
                fontSize="$3"
                fontWeight="700"
                textTransform="uppercase"
                letterSpacing={0.5}
              >
                Provenance & Clinical Trust
              </Text>

              <XStack alignItems="center" gap="$2" flexWrap="wrap">
                <SourceBadge
                  source={provenance.source === 'manual' ? 'manual' : 'ai_extracted'}
                  confidence={
                    provenance.confidence != null
                      ? Math.round(provenance.confidence * 100)
                      : undefined
                  }
                />
                {provenance.risk_level ? (
                  <RiskBadge level={provenance.risk_level as RiskLevel} />
                ) : null}
              </XStack>

              {provenance.source_display ? (
                <Text color="$color11" fontSize="$3">
                  Origin: {provenance.source_display}
                </Text>
              ) : null}

              {provenance.has_source_document ? (
                <XStack alignItems="center" gap="$2">
                  <Text fontSize={14}>📄</Text>
                  <Paragraph color="$color10" size="$2">
                    Verified clinical document on file.
                  </Paragraph>
                </XStack>
              ) : null}
            </YStack>

            <Button
              theme="blue"
              onPress={() => onOpenChange(false)}
              marginTop="$2"
            >
              Close
            </Button>
          </YStack>
        )}
      </Sheet.Frame>
    </Sheet>
  )
}
