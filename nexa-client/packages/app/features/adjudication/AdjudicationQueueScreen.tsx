'use client'

import {
  Button,
  Card,
  H2,
  Input,
  Paragraph,
  ScrollView,
  Separator,
  Spinner,
  Text,
  XStack,
  YStack,
} from '@my/ui'
import { useRouter } from 'next/navigation'
import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, NexaApiClient, type AdjudicationCaseResponse } from '../../utils/apiClient'
import {
  bindAdjudicationWorkflow,
  completeAdjudicationCreation,
  createReviewSessionId,
  getAdjudicationWorkflow,
  prepareAdjudicationCreation,
} from '../../services/adjudicationWorkflowStore'
import { useProviderAuth } from '../doctor/ProviderAuthContext'

const STATUS_LABELS: Record<AdjudicationCaseResponse['status'], string> = {
  PENDING: 'Pending source review',
  ACCEPTED: 'Accepted by human reviewer',
  REJECTED: 'Rejected',
  NEEDS_SPECIALIST_REVIEW: 'Specialist review requested',
}

export function AdjudicationQueueScreen() {
  const router = useRouter()
  const { hydrated, isAuthenticated, roles } = useProviderAuth()
  const [cases, setCases] = useState<AdjudicationCaseResponse[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [routingId, setRoutingId] = useState('')
  const [jobId, setJobId] = useState('')
  const [creating, setCreating] = useState<'route' | 'job' | null>(null)
  const creatingRef = useRef(false)
  const clinicallyQualified = roles.some((role) =>
    ['clinician', 'clinical_reviewer'].includes(role)
  )

  const loadCases = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setCases(await NexaApiClient.listAdjudicationCases())
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) {
        router.replace('/doctor/login')
        return
      }
      setError('The adjudication queue could not be loaded.')
    } finally {
      setLoading(false)
    }
  }, [router])

  useEffect(() => {
    if (hydrated && !isAuthenticated) router.replace('/doctor/login')
    if (isAuthenticated) void loadCases()
  }, [hydrated, isAuthenticated, loadCases, router])

  const createCase = async (source: 'route' | 'job') => {
    if (creatingRef.current || !clinicallyQualified) return
    const sourceId = (source === 'route' ? routingId : jobId).trim()
    if (!sourceId) {
      setError(`Enter the eligible ${source === 'route' ? 'routing' : 'job'} reference.`)
      return
    }
    creatingRef.current = true
    setCreating(source)
    setError(null)
    const fingerprint = `${source}:${sourceId}`
    const operation = prepareAdjudicationCreation(fingerprint)
    try {
      const payload = {
        review_session_id: operation.reviewSessionId,
        idempotency_key: operation.idempotencyKey,
      }
      const created =
        source === 'route'
          ? await NexaApiClient.createAdjudicationCaseFromRoute(sourceId, payload)
          : await NexaApiClient.createDocumentAdjudicationCase(sourceId, payload)
      bindAdjudicationWorkflow(created.case_id, operation.reviewSessionId)
      completeAdjudicationCreation(fingerprint)
      router.push(`/doctor/pipeline/adjudication/${encodeURIComponent(created.case_id)}/review`)
    } catch (reason) {
      if (reason instanceof ApiError) {
        if (reason.code === 'ADJUDICATION_CONSENT_INACTIVE') {
          completeAdjudicationCreation(fingerprint)
          setError('Document-review consent is no longer active. Request access again.')
          return
        }
        if (
          [
            'ADJUDICATION_ERASURE_ACCESS_BLOCKED',
            'ADJUDICATION_ERASURE_REGISTRY_UNAVAILABLE',
          ].includes(reason.code ?? '')
        ) {
          completeAdjudicationCreation(fingerprint)
          setError('Protected review access is unavailable. Request authorized access again.')
          return
        }
        if (reason.code === 'ADJUDICATION_ROUTE_INELIGIBLE') {
          setError('Only an ordinary SOURCE_ONLY retained route can be reviewed here.')
          return
        }
      }
      setError('The adjudication case could not be created.')
    } finally {
      creatingRef.current = false
      setCreating(null)
    }
  }

  if (!hydrated || !isAuthenticated) {
    return (
      <YStack
        flex={1}
        alignItems="center"
        justifyContent="center"
      >
        <Spinner size="large" />
      </YStack>
    )
  }

  return (
    <ScrollView backgroundColor="$background">
      <YStack
        padding="$5"
        gap="$4"
        maxWidth={1000}
        width="100%"
        marginHorizontal="auto"
      >
        <H2>Source adjudication</H2>
        <Paragraph>
          Review archived SOURCE_ONLY documents. Extraction routing is not a clinical proposal and
          no AI-derived value is shown here.
        </Paragraph>

        {clinicallyQualified ? (
          <Card
            borderWidth={1}
            padding="$4"
            gap="$3"
          >
            <Text fontWeight="700">Start an authorized review</Text>
            <Paragraph size="$2">
              Use an eligible retained routing reference, or a source-only job with no supported
              candidates. Patient identifiers are derived by the server.
            </Paragraph>
            <Input
              aria-label="Eligible routing reference"
              value={routingId}
              onChangeText={setRoutingId}
              placeholder="Eligible routing reference"
              autoCapitalize="none"
            />
            <Button
              disabled={creating !== null}
              onPress={() => void createCase('route')}
            >
              {creating === 'route' ? 'Creating…' : 'Create field-linked case'}
            </Button>
            <Separator />
            <Input
              aria-label="Zero-candidate job reference"
              value={jobId}
              onChangeText={setJobId}
              placeholder="Zero-candidate job reference"
              autoCapitalize="none"
            />
            <Button
              disabled={creating !== null}
              onPress={() => void createCase('job')}
            >
              {creating === 'job' ? 'Creating…' : 'Create document-level case'}
            </Button>
          </Card>
        ) : (
          <Card
            borderWidth={1}
            padding="$4"
          >
            <Paragraph>
              Your role may view operational case status but cannot enter or commit clinical
              information.
            </Paragraph>
          </Card>
        )}

        {error ? (
          <Card
            backgroundColor="$red2"
            padding="$3"
            role="alert"
          >
            <Paragraph color="$red10">{error}</Paragraph>
          </Card>
        ) : null}

        <Separator />
        <XStack
          justifyContent="space-between"
          alignItems="center"
        >
          <Text fontWeight="700">Cases</Text>
          <Button
            size="$2"
            disabled={loading}
            onPress={() => void loadCases()}
          >
            Refresh
          </Button>
        </XStack>
        {loading ? <Spinner /> : null}
        {!loading && cases.length === 0 ? (
          <Paragraph>No ordinary SOURCE_ONLY adjudication cases are available.</Paragraph>
        ) : null}
        {cases.map((item) => {
          const workflow = getAdjudicationWorkflow(item.case_id)
          const committed = Boolean(item.clinical_committed_at)
          return (
            <Card
              key={item.case_id}
              borderWidth={1}
              padding="$4"
              gap="$2"
            >
              <XStack
                justifyContent="space-between"
                flexWrap="wrap"
                gap="$2"
              >
                <Text fontWeight="700">{STATUS_LABELS[item.status]}</Text>
                <Text>{item.routing_id ? 'Field-linked case' : 'Document-level case'}</Text>
              </XStack>
              <Paragraph size="$2">Created {new Date(item.created_at).toLocaleString()}</Paragraph>
              <Paragraph size="$2">
                {committed
                  ? `Committed ${new Date(item.clinical_committed_at!).toLocaleString()}`
                  : item.resolved_at
                    ? `Resolved ${new Date(item.resolved_at).toLocaleString()}`
                    : 'Action pending'}
              </Paragraph>
              {workflow ? (
                <Button
                  disabled={!clinicallyQualified}
                  onPress={() =>
                    router.push(
                      `/doctor/pipeline/adjudication/${encodeURIComponent(item.case_id)}/${
                        workflow.submission ? 'result' : 'review'
                      }`
                    )
                  }
                >
                  {workflow.submission ? 'View result' : 'Continue review'}
                </Button>
              ) : item.status === 'PENDING' && clinicallyQualified ? (
                <Button
                  disabled={creating !== null}
                  onPress={() => {
                    if (creatingRef.current) return
                    creatingRef.current = true
                    setCreating('route')
                    setError(null)
                    const reviewSessionId = createReviewSessionId()
                    void NexaApiClient.recoverAdjudicationSession(item.case_id, reviewSessionId)
                      .then(() => {
                        bindAdjudicationWorkflow(item.case_id, reviewSessionId)
                        router.push(
                          `/doctor/pipeline/adjudication/${encodeURIComponent(item.case_id)}/review`
                        )
                      })
                      .catch((reason) => {
                        if (
                          reason instanceof ApiError &&
                          [
                            'ADJUDICATION_ERASURE_ACCESS_BLOCKED',
                            'ADJUDICATION_ERASURE_REGISTRY_UNAVAILABLE',
                            'ADJUDICATION_CONSENT_INACTIVE',
                          ].includes(reason.code ?? '')
                        ) {
                          setError(
                            'Protected review access is unavailable. Request authorized access again.'
                          )
                        } else {
                          setError('The pending review session could not be recovered.')
                        }
                      })
                      .finally(() => {
                        creatingRef.current = false
                        setCreating(null)
                      })
                  }}
                >
                  Recover pending review
                </Button>
              ) : (
                <Paragraph
                  size="$2"
                  color="$orange10"
                >
                  This browser no longer holds the authoritative review session. Reopen the case
                  through the authorized intake workflow; a new session will not be invented.
                </Paragraph>
              )}
            </Card>
          )
        })}
      </YStack>
    </ScrollView>
  )
}
