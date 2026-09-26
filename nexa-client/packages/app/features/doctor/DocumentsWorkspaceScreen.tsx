'use client'

import {
  ActionButton,
  Card,
  InlineNotice,
  LoadingState,
  Paragraph,
  ScreenContainer,
  ScreenHeader,
  SectionHeading,
  StatusBadge,
  Surface,
  Text,
  XStack,
  YStack,
} from '@my/ui'
import {
  AlertCircle,
  ArrowRight,
  CheckCircle,
  Clock,
  FilePlus,
  FileText,
  Filter,
} from '@tamagui/lucide-icons'
import { useRouter, useSearchParams } from 'next/navigation'
import { useCallback, useEffect, useState } from 'react'
import {
  ApiError,
  NexaApiClient,
  type AdjudicationCaseResponse,
} from '../../utils/apiClient'
import { useProviderAuth } from './ProviderAuthContext'

type DocumentFilterTab = 'all' | 'needs_review' | 'processing' | 'completed'

const FILTERS: Array<{ value: DocumentFilterTab; label: string }> = [
  { value: 'all', label: 'All' },
  { value: 'needs_review', label: 'Needs Review' },
  { value: 'processing', label: 'Processing' },
  { value: 'completed', label: 'Completed' },
]

function safePatientLabel(patientId: string): string {
  if (!patientId) return 'Patient Record'
  if (patientId.startsWith('NC-')) return patientId
  return `Patient #${patientId.slice(0, 8)}`
}

function friendlyDocumentType(sourceDocId: string): string {
  const lower = sourceDocId.toLowerCase()
  if (lower.includes('lab') || lower.includes('blood') || lower.includes('test')) return 'Lab Report'
  if (lower.includes('rx') || lower.includes('presc')) return 'Prescription'
  if (lower.includes('discharge')) return 'Discharge Summary'
  if (lower.includes('radiology') || lower.includes('xray') || lower.includes('scan')) return 'Imaging Report'
  return 'External Medical Record'
}

export function DocumentsWorkspaceScreen() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const { hydrated, isAuthenticated, discoverySelection, accessGrant } = useProviderAuth()

  const initialTab = (searchParams.get('tab') as DocumentFilterTab) || 'all'
  const [filterTab, setFilterTab] = useState<DocumentFilterTab>(
    initialTab === 'needs_review' || initialTab === 'processing' || initialTab === 'completed'
      ? initialTab
      : 'all'
  )

  const [cases, setCases] = useState<AdjudicationCaseResponse[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const loadDocuments = useCallback(async () => {
    if (!isAuthenticated) return
    setLoading(true)
    setError(null)
    try {
      const items = await NexaApiClient.listAdjudicationCases()
      setCases(items)
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        router.replace('/doctor/login')
        return
      }
      setError('Documents list is temporarily unavailable. Please try again.')
    } finally {
      setLoading(false)
    }
  }, [isAuthenticated, router])

  useEffect(() => {
    if (hydrated && !isAuthenticated) {
      router.replace('/doctor/login')
    } else if (isAuthenticated) {
      void loadDocuments()
    }
  }, [hydrated, isAuthenticated, loadDocuments, router])

  const handleAddDocument = () => {
    // If patient context already exists in memory, proceed directly to upload
    if (accessGrant?.patientId || discoverySelection?.discoveryHandle) {
      router.push('/doctor/pipeline/upload')
    } else {
      // Otherwise, request patient selection first
      router.push('/doctor/patient-search?intent=document_upload')
    }
  }

  const filteredCases = cases.filter((item) => {
    if (filterTab === 'needs_review') return item.status === 'PENDING'
    if (filterTab === 'processing') return false // Mock/future active pipeline jobs
    if (filterTab === 'completed') return item.status === 'ACCEPTED' || item.status === 'REJECTED'
    return true
  })

  const needsReviewCount = cases.filter((c) => c.status === 'PENDING').length

  return (
    <ScreenContainer>
      <ScreenHeader
        title="Documents"
        description="Review and manage external medical records, imported lab results, and previous prescriptions."
        action={
          <ActionButton intent="primary" onPress={handleAddDocument}>
            <XStack alignItems="center" gap="$2">
              <FilePlus size={18} color="$nexaOnAccent" />
              <Text color="$nexaOnAccent" fontWeight="700">
                + Add Patient Document
              </Text>
            </XStack>
          </ActionButton>
        }
      />

      {/* Filter Tabs */}
      <XStack gap="$2" flexWrap="wrap">
        {FILTERS.map((tab) => {
          const isSelected = filterTab === tab.value
          return (
            <ActionButton
              key={tab.value}
              size="$3"
              intent={isSelected ? 'primary' : undefined}
              onPress={() => setFilterTab(tab.value)}
            >
              <XStack alignItems="center" gap="$1.5">
                <Text color={isSelected ? '$nexaOnAccent' : '$nexaText'} fontWeight="700">
                  {tab.label}
                  {tab.value === 'all' && ` (${cases.length})`}
                  {tab.value === 'processing' && ' (0)'}
                </Text>
                {tab.value === 'needs_review' && needsReviewCount > 0 && (
                  <StatusBadge tone="warning">
                    {needsReviewCount}
                  </StatusBadge>
                )}
              </XStack>
            </ActionButton>
          )
        })}
      </XStack>

      {error && <InlineNotice title={error} tone="danger" />}

      {/* Content Section */}
      {loading ? (
        <Surface padding="$6" alignItems="center" justifyContent="center">
          <LoadingState label="Loading patient documents..." />
        </Surface>
      ) : filteredCases.length === 0 ? (
        <Surface padding="$6" alignItems="center" justifyContent="center" gap="$3" borderRadius={14}>
          <FileText size={40} color="$nexaSecondary" />
          <SectionHeading>
            {filterTab === 'needs_review'
              ? 'No documents need review'
              : filterTab === 'completed'
                ? 'No completed documents yet'
                : 'No external documents found'}
          </SectionHeading>
          <Paragraph color="$nexaSecondary" textAlign="center" maxWidth={480}>
            {filterTab === 'needs_review'
              ? 'All imported records have been verified by a clinician. New uploaded documents will appear here.'
              : 'Add an external medical record, lab report, or prescription to import it into the patient record.'}
          </Paragraph>
          <ActionButton intent="primary" onPress={handleAddDocument}>
            + Add Patient Document
          </ActionButton>
        </Surface>
      ) : (
        <YStack gap="$3">
          {filteredCases.map((item) => {
            const isPending = item.status === 'PENDING'
            const isAccepted = item.status === 'ACCEPTED'
            const docType = friendlyDocumentType(item.source_document_id)
            const patientDisplay = safePatientLabel(item.patient_id)

            return (
              <Surface
                key={item.case_id}
                padding="$4.5"
                borderRadius={12}
                backgroundColor="$nexaSurface"
                borderColor="$nexaBorder"
                hoverStyle={{ borderColor: '$nexaAccent' }}
                gap="$3"
              >
                <XStack
                  alignItems="center"
                  justifyContent="space-between"
                  flexWrap="wrap"
                  gap="$3"
                >
                  <YStack gap="$1.5">
                    <XStack alignItems="center" gap="$2.5" flexWrap="wrap">
                      <Text color="$nexaText" fontWeight="800" fontSize={16}>
                        {patientDisplay}
                      </Text>
                      <StatusBadge tone="neutral">
                        {docType}
                      </StatusBadge>
                      {isPending ? (
                        <StatusBadge tone="warning">
                          Needs clinical verification
                        </StatusBadge>
                      ) : isAccepted ? (
                        <StatusBadge tone="success">
                          Verified & Added
                        </StatusBadge>
                      ) : (
                        <StatusBadge tone="danger">
                          Rejected
                        </StatusBadge>
                      )}
                    </XStack>

                    <XStack gap="$3" alignItems="center" flexWrap="wrap">
                      <XStack alignItems="center" gap="$1.5">
                        <Clock size={14} color="$nexaSecondary" />
                        <Text color="$nexaSecondary" fontSize={12}>
                          Uploaded: {new Date(item.created_at).toLocaleDateString()}
                        </Text>
                      </XStack>
                    </XStack>

                    {isPending && (
                      <Paragraph color="$nexaSecondary" fontSize={13}>
                        This document needs clinical verification before information is added to the patient record.
                      </Paragraph>
                    )}
                  </YStack>

                  <XStack gap="$2" alignItems="center">
                    {isPending ? (
                      <ActionButton
                        intent="primary"
                        onPress={() =>
                          router.push(
                            `/doctor/pipeline/adjudication/${encodeURIComponent(item.case_id)}/review`
                          )
                        }
                      >
                        <XStack alignItems="center" gap="$1.5">
                          <Text color="$nexaOnAccent" fontWeight="700">
                            Review Document
                          </Text>
                          <ArrowRight size={14} color="$nexaOnAccent" />
                        </XStack>
                      </ActionButton>
                    ) : (
                      <ActionButton
                        onPress={() => router.push('/doctor/patient-record')}
                      >
                        View Record
                      </ActionButton>
                    )}
                  </XStack>
                </XStack>
              </Surface>
            )
          })}
        </YStack>
      )}
    </ScreenContainer>
  )
}
