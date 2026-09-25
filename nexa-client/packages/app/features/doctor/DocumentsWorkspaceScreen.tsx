'use client'

import {
  ActionButton,
  InlineNotice,
  LoadingState,
  Paragraph,
  ScreenContainer,
  ScreenHeader,
  StatusBadge,
  Surface,
  Text,
  XStack,
  YStack,
} from '@my/ui'
import { FilePlus2, RefreshCw } from '@tamagui/lucide-icons'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { ApiError, NexaApiClient, type AdjudicationCaseResponse } from '../../utils/apiClient'
import { useProviderAuth } from './ProviderAuthContext'

type DocumentFilter = 'all' | 'needs_review' | 'processing' | 'completed'

const FILTERS: Array<{ value: DocumentFilter; label: string }> = [
  { value: 'all', label: 'All' },
  { value: 'needs_review', label: 'Needs Review' },
  { value: 'processing', label: 'Processing' },
  { value: 'completed', label: 'Completed' },
]

function documentBucket(item: AdjudicationCaseResponse): Exclude<DocumentFilter, 'all'> {
  if (item.clinical_committed_at || item.status === 'REJECTED') return 'completed'
  if (item.status === 'ACCEPTED' && !item.clinical_committed_at) return 'processing'
  return 'needs_review'
}

function documentStatus(item: AdjudicationCaseResponse): {
  label: string
  tone: 'warning' | 'info' | 'success'
} {
  if (item.clinical_committed_at) return { label: 'Added to Patient Record', tone: 'success' }
  if (item.status === 'REJECTED') return { label: 'Review Complete — Not Added', tone: 'info' }
  if (item.status === 'ACCEPTED') return { label: 'Verified — Ready to Add', tone: 'info' }
  if (item.status === 'NEEDS_SPECIALIST_REVIEW') return { label: 'Specialist Review Needed', tone: 'warning' }
  return { label: 'Needs Clinical Verification', tone: 'warning' }
}

function workspaceError(reason: unknown): string {
  if (!(reason instanceof ApiError)) return 'Documents could not be loaded. Try again.'
  if (reason.code === 'CLINICAL_ELIGIBILITY_DENIED' || reason.status === 403) {
    return 'Document review is not currently authorized for this provider account. Review provider verification status or contact your clinical administrator.'
  }
  if (reason.status === 401) return 'Your provider session expired. Sign in again to open Documents.'
  if (reason.status >= 500 || reason.status === 0) {
    return 'Documents are temporarily unavailable because a required service cannot be reached.'
  }
  return 'Documents could not be loaded. Try again.'
}

export function DocumentsWorkspaceScreen() {
  const router = useRouter()
  const { hydrated, isAuthenticated } = useProviderAuth()
  const [filter, setFilter] = useState<DocumentFilter>('all')
  const [items, setItems] = useState<AdjudicationCaseResponse[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!isAuthenticated) return
    setLoading(true)
    setError(null)
    try {
      setItems(await NexaApiClient.listAdjudicationCases())
    } catch (reason) {
      setError(workspaceError(reason))
    } finally {
      setLoading(false)
    }
  }, [isAuthenticated])

  useEffect(() => {
    if (hydrated && !isAuthenticated) {
      router.replace('/doctor/login')
      return
    }
    if (hydrated && isAuthenticated) void load()
  }, [hydrated, isAuthenticated, load, router])

  const visible = useMemo(
    () => items.filter((item) => filter === 'all' || documentBucket(item) === filter),
    [filter, items]
  )

  if (!hydrated || (loading && !items.length)) return <LoadingState label="Opening Documents..." />

  return (
    <ScreenContainer>
      <ScreenHeader
        eyebrow="CLINICAL DOCUMENTS"
        title="Documents"
        description="Import external records, see what needs clinical verification, and track what has been added to patient records."
        action={
          <ActionButton onPress={() => router.push('/doctor/patient-search?intent=document_upload')}>
            <XStack gap="$2" alignItems="center"><FilePlus2 size={17} /> <Text>Import Document</Text></XStack>
          </ActionButton>
        }
      />

      <XStack gap="$2" flexWrap="wrap">
        {FILTERS.map((item) => (
          <ActionButton
            key={item.value}
            intent={filter === item.value ? 'primary' : undefined}
            onPress={() => setFilter(item.value)}
          >
            {item.label}
          </ActionButton>
        ))}
        <ActionButton onPress={() => void load()} disabled={loading}>
          <XStack gap="$2" alignItems="center"><RefreshCw size={16} /> <Text>Refresh</Text></XStack>
        </ActionButton>
      </XStack>

      {error ? <InlineNotice tone="danger" title="Documents unavailable">{error}</InlineNotice> : null}

      {!loading && !error && visible.length === 0 ? (
        <Surface padding="$6" alignItems="center" gap="$2">
          <Text fontSize={18} fontWeight="800">No documents in this view</Text>
          <Paragraph color="$nexaSecondary">
            Imported records will appear here as they move through verification and into the patient record.
          </Paragraph>
        </Surface>
      ) : null}

      <YStack gap="$3">
        {visible.map((item) => {
          const status = documentStatus(item)
          const bucket = documentBucket(item)
          return (
            <Surface key={item.case_id} padding="$4" gap="$3">
              <XStack justifyContent="space-between" alignItems="center" flexWrap="wrap" gap="$2">
                <YStack gap="$1">
                  <Text fontWeight="800" fontSize={16}>Imported record</Text>
                  <Paragraph color="$nexaSecondary" fontSize={13}>
                    Received {new Date(item.created_at).toLocaleString()}
                  </Paragraph>
                </YStack>
                <StatusBadge tone={status.tone}>{status.label}</StatusBadge>
              </XStack>
              {item.clinical_committed_at ? (
                <Paragraph color="$nexaSecondary">
                  Added to the patient record {new Date(item.clinical_committed_at).toLocaleString()}.
                </Paragraph>
              ) : item.resolved_at ? (
                <Paragraph color="$nexaSecondary">
                  Clinician review completed {new Date(item.resolved_at).toLocaleString()}.
                </Paragraph>
              ) : (
                <Paragraph color="$nexaSecondary">
                  The original document must be reviewed before any imported information can be added.
                </Paragraph>
              )}
              {bucket !== 'completed' ? (
                <ActionButton onPress={() => router.push('/doctor/pipeline/adjudication')}>
                  {bucket === 'processing' ? 'Add to Patient Record' : 'Review Document'}
                </ActionButton>
              ) : null}
            </Surface>
          )
        })}
      </YStack>
    </ScreenContainer>
  )
}
