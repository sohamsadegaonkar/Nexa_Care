'use client'

import {
  ActionButton,
  Card,
  H2,
  InlineNotice,
  LoadingState,
  Paragraph,
  ScrollView,
  StatusBadge,
  Text,
  XStack,
  YStack,
} from '@my/ui'
import {
  ArrowRight,
  ClipboardCheck,
  Clock,
  FileText,
  ShieldCheck,
} from '@tamagui/lucide-icons'
import { useRouter } from 'next/navigation'
import { useCallback, useEffect, useState } from 'react'
import { ApiError, NexaApiClient, type AdjudicationCaseResponse } from '../../utils/apiClient'
import { clearAllAdjudicationWorkflows } from '../../services/adjudicationWorkflowStore'
import { useProviderAuth } from '../doctor/ProviderAuthContext'
import { isTerminalAdjudicationAccessError } from './adjudicationAccess'

function friendlyDocType(sourceDocId: string): string {
  const lower = sourceDocId.toLowerCase()
  if (lower.includes('lab') || lower.includes('blood') || lower.includes('test')) return 'Lab Report'
  if (lower.includes('rx') || lower.includes('presc')) return 'Prescription'
  if (lower.includes('discharge')) return 'Discharge Summary'
  return 'External Record'
}

function safePatientDisplay(patientId: string): string {
  if (!patientId || patientId === 'redacted-by-ui') return 'Patient'
  if (patientId.startsWith('NC-')) return patientId
  return `Patient #${patientId.slice(0, 8)}`
}

export function AdjudicationQueueScreen() {
  const router = useRouter()
  const { hydrated, isAuthenticated, roles } = useProviderAuth()
  const [cases, setCases] = useState<AdjudicationCaseResponse[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const clinicallyQualified = roles.some((role) =>
    ['clinician', 'clinical_reviewer'].includes(role)
  )

  const loadCases = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setCases(await NexaApiClient.listAdjudicationCases())
    } catch (reason) {
      if (isTerminalAdjudicationAccessError(reason)) {
        clearAllAdjudicationWorkflows()
        setError('Your access to these records has expired. Request access again.')
        return
      }
      if (reason instanceof ApiError && reason.status === 401) {
        router.replace('/doctor/login')
        return
      }
      setError('The review queue could not be loaded.')
    } finally {
      setLoading(false)
    }
  }, [router])

  useEffect(() => {
    if (hydrated && !isAuthenticated) router.replace('/doctor/login')
    if (isAuthenticated) void loadCases()
  }, [hydrated, isAuthenticated, loadCases, router])

  if (!hydrated || !isAuthenticated) {
    return (
      <YStack
        flex={1}
        alignItems="center"
        justifyContent="center"
      >
        <LoadingState label="Loading review inbox..." />
      </YStack>
    )
  }

  const pendingCases = cases.filter((c) => c.status === 'PENDING')

  return (
    <ScrollView backgroundColor="$background">
      <YStack
        padding="$5"
        gap="$4"
        maxWidth={1000}
        width="100%"
        marginHorizontal="auto"
      >
        <XStack alignItems="center" gap="$3">
          <YStack
            padding="$2.5"
            borderRadius={12}
            backgroundColor="$nexaAccentSoft"
          >
            <ClipboardCheck size={28} color="$nexaAccent" />
          </YStack>
          <YStack gap="$0.5">
            <H2>Review Imported Records</H2>
            <Paragraph color="$nexaSecondary">
              Review information from uploaded medical documents before it is added to the patient's record.
            </Paragraph>
          </YStack>
        </XStack>

        {!clinicallyQualified && (
          <Card
            borderWidth={1}
            padding="$4"
            borderRadius={10}
            backgroundColor="$nexaMuted"
            borderColor="$nexaBorder"
          >
            <Paragraph color="$nexaSecondary">
              Your role may view operational case status but cannot enter or commit clinical information.
            </Paragraph>
          </Card>
        )}

        {error && <InlineNotice title={error} tone="danger" />}

        {loading ? (
          <Card padding="$6" alignItems="center" justifyContent="center">
            <LoadingState label="Checking records needing review..." />
          </Card>
        ) : !clinicallyQualified ? null : cases.length === 0 ? (
          <Card
            borderWidth={1}
            borderColor="$nexaBorder"
            borderRadius={12}
            padding="$6"
            alignItems="center"
            gap="$3"
          >
            <FileText size={36} color="$nexaSecondary" />
            <Text fontWeight="800" fontSize={16} color="$nexaText">
              No records currently need clinical review
            </Text>
            <Paragraph color="$nexaSecondary" textAlign="center" maxWidth={460}>
              When new external documents are uploaded and require doctor verification, they will appear here.
            </Paragraph>
            <ActionButton onPress={() => router.push('/doctor/documents')}>
              Open Documents Workspace
            </ActionButton>
          </Card>
        ) : (
          <YStack gap="$3">
            <XStack justifyContent="space-between" alignItems="center">
              <Text fontWeight="700" fontSize={16} color="$nexaText">
                Records requiring review
              </Text>
              <StatusBadge tone={pendingCases.length > 0 ? 'warning' : 'neutral'}>
                {pendingCases.length} needing review
              </StatusBadge>
            </XStack>

            {cases.map((item) => {
              const docType = friendlyDocType(item.source_document_id)
              const patientDisplay = safePatientDisplay(item.patient_id)
              const isPending = item.status === 'PENDING'

              return (
                <Card
                  key={item.case_id}
                  borderWidth={1}
                  borderColor="$nexaBorder"
                  borderRadius={12}
                  padding="$4"
                  backgroundColor="$nexaSurface"
                  gap="$3"
                  hoverStyle={{ borderColor: '$nexaAccent' }}
                >
                  <XStack
                    justifyContent="space-between"
                    alignItems="center"
                    flexWrap="wrap"
                    gap="$3"
                  >
                    <YStack gap="$1.5">
                      <XStack alignItems="center" gap="$2.5" flexWrap="wrap">
                        <Text fontWeight="800" fontSize={16} color="$nexaText">
                          {docType}
                        </Text>
                        <Text color="$nexaSecondary" fontSize={14}>
                          Patient: {patientDisplay}
                        </Text>
                        {isPending ? (
                          <StatusBadge tone="warning">Needs Clinical Verification</StatusBadge>
                        ) : item.status === 'ACCEPTED' ? (
                          <StatusBadge tone="success">Verified & Added</StatusBadge>
                        ) : (
                          <StatusBadge tone="danger">Rejected</StatusBadge>
                        )}
                      </XStack>

                      <XStack gap="$3" alignItems="center">
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
                        chromeless
                        onPress={() => router.push('/doctor/documents')}
                      >
                        View in Documents
                      </ActionButton>
                    )}
                  </XStack>
                </Card>
              )
            })}
          </YStack>
        )}

        <Card
          backgroundColor="$nexaMuted"
          borderColor="$nexaBorder"
          padding="$4"
          borderRadius={10}
        >
          <XStack gap="$3" alignItems="center">
            <ShieldCheck size={22} color="$nexaAccent" />
            <YStack gap="$1" flex={1}>
              <Text fontWeight="700" fontSize={13} color="$nexaText">
                Clinical Safety Boundary
              </Text>
              <Paragraph color="$nexaSecondary" fontSize={12} lineHeight={18}>
                Detected information from external documents is never treated as verified clinical truth without explicit doctor verification.
              </Paragraph>
            </YStack>
          </XStack>
        </Card>
      </YStack>
    </ScrollView>
  )
}
