'use client'

import { Button, Card, H2, Paragraph, Spinner, Text, YStack } from '@my/ui'
import { useParams, useRouter } from 'next/navigation'
import { useEffect, useState } from 'react'
import { ApiError, NexaApiClient, type AdjudicationCaseResponse } from '../../utils/apiClient'
import {
  clearAdjudicationWorkflow,
  isCommitEligible,
  recordAdjudicationCommit,
  useAdjudicationWorkflow,
} from '../../services/adjudicationWorkflowStore'
import { useProviderAuth } from '../doctor/ProviderAuthContext'
import { isTerminalAdjudicationAccessError } from './adjudicationAccess'

export function AdjudicationResultScreen() {
  const params = useParams<{ caseId: string }>()
  const caseId = String(params.caseId)
  const router = useRouter()
  const { hydrated, isAuthenticated, roles } = useProviderAuth()
  const workflow = useAdjudicationWorkflow(caseId)
  const [caseDetail, setCaseDetail] = useState<AdjudicationCaseResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [confirming, setConfirming] = useState(false)
  const [committing, setCommitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const clinicallyQualified = roles.some((role) =>
    ['clinician', 'clinical_reviewer'].includes(role)
  )

  useEffect(() => {
    if (hydrated && !isAuthenticated) router.replace('/doctor/login')
  }, [hydrated, isAuthenticated, router])

  useEffect(() => {
    if (!isAuthenticated || !workflow) {
      setLoading(false)
      return
    }
    let active = true
    void NexaApiClient.getAdjudicationCase(caseId)
      .then((result) => {
        if (active) setCaseDetail(result)
      })
      .catch((reason) => {
        if (!active) return
        if (isTerminalAdjudicationAccessError(reason)) {
          clearAdjudicationWorkflow(caseId)
          setError('Result access expired. Reopen the authorized workflow.')
          return
        }
        setError('The current document review could not be loaded.')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [caseId, isAuthenticated, workflow])

  const commit = async () => {
    if (!workflow?.submission || committing || !clinicallyQualified) return
    setCommitting(true)
    setError(null)
    try {
      const result = await NexaApiClient.commitAdjudicationSubmission(
        workflow.submission.submission_id,
        workflow.reviewSessionId
      )
      recordAdjudicationCommit(caseId, result.committed_at)
      setCaseDetail((current) =>
        current ? { ...current, clinical_committed_at: result.committed_at } : current
      )
      setConfirming(false)
    } catch (reason) {
      if (isTerminalAdjudicationAccessError(reason)) {
        clearAdjudicationWorkflow(caseId)
        setError('Commit access expired. Reopen the authorized workflow.')
      } else if (reason instanceof ApiError && reason.code === 'ADJUDICATION_NOT_ACCEPTED') {
        setError('Only the current accepted human submission can be committed.')
      } else {
        setError('The clinical commit did not complete. No success has been assumed.')
      }
    } finally {
      setCommitting(false)
    }
  }

  if (!hydrated || loading) return <Spinner size="large" />
  if (!isAuthenticated) return null
  if (!workflow?.submission) {
    return (
      <YStack
        padding="$6"
        gap="$3"
        role="alert"
      >
        <H2>Review session unavailable</H2>
        <Paragraph>
          The in-memory review result is unavailable after refresh or session loss. Reopen the case
          safely from the review queue.
        </Paragraph>
        <Button onPress={() => router.replace('/doctor/pipeline/adjudication')}>
          Back to cases
        </Button>
      </YStack>
    )
  }

  const committedAt = workflow.committedAt ?? caseDetail?.clinical_committed_at ?? null
  const canCommit =
    clinicallyQualified &&
    caseDetail?.status === 'ACCEPTED' &&
    isCommitEligible(workflow.submission.outcome, committedAt)

  return (
    <YStack
      padding="$6"
      gap="$4"
      maxWidth={720}
      width="100%"
      marginHorizontal="auto"
    >
      <H2>Review Complete</H2>
      <Card
        borderWidth={1}
        padding="$4"
        gap="$2"
      >
        <Text fontWeight="700">
          {workflow.submission.outcome === 'ACCEPTED'
            ? 'Human verification accepted'
            : workflow.submission.outcome === 'REJECTED'
              ? 'Source item rejected'
              : 'Specialist review requested'}
        </Text>
        <Paragraph>
          This review confirms what was verified from the source document. Nothing is added to the patient record until you confirm the final step.
        </Paragraph>
        {committedAt ? (
          <>
            <Paragraph color="$green10">Verified information added to the patient record.</Paragraph>
            <Paragraph>Committed {new Date(committedAt).toLocaleString()}</Paragraph>
            <Paragraph>Verified by clinician</Paragraph>
          </>
        ) : null}
      </Card>

      {error ? (
        <Paragraph
          color="$red10"
          role="alert"
        >
          {error}
        </Paragraph>
      ) : null}

      {canCommit && !confirming ? (
        <Button
          theme="blue"
          disabled={committing}
          onPress={() => setConfirming(true)}
        >
          Add to Patient Record
        </Button>
      ) : null}
      {canCommit && confirming ? (
        <Card
          borderWidth={1}
          padding="$4"
          gap="$3"
        >
          <Text fontWeight="700">Add to Patient Record</Text>
          <Paragraph>
            Add the verified information to the patient record. The original document remains the source of truth for this review.
          </Paragraph>
          <Button
            theme="blue"
            disabled={committing}
            onPress={() => void commit()}
          >
            {committing ? 'Adding…' : 'Confirm Add to Patient Record'}
          </Button>
          <Button
            disabled={committing}
            onPress={() => setConfirming(false)}
          >
            Cancel
          </Button>
        </Card>
      ) : null}

      {!canCommit && !committedAt ? (
        <Paragraph>
          This review outcome cannot be added to the patient record. Rejected items and cases sent for specialist review remain unchanged.
        </Paragraph>
      ) : null}

      <Card
        backgroundColor="$backgroundHover"
        padding="$3"
      >
        <Paragraph size="$2">
          Supersession is unavailable in this workspace because the current safe case-detail API
          does not return the accepted structured submission needed for full reconfirmation.
        </Paragraph>
      </Card>
      <Button onPress={() => router.replace('/doctor/pipeline/adjudication')}>Back to Reviews</Button>
    </YStack>
  )
}
