import { screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithTamagui } from '../../../../test/test-utils'
import { NexaApiClient, type ExtractionJobStatusResponse } from '../../utils/apiClient'
import { JobStatusScreen } from './JobStatusScreen'

const push = vi.fn()

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push }),
  useSearchParams: () => ({
    get: (key: string) => (key === 'workflow_id' ? 'workflow-1' : null),
  }),
  useParams: () => ({ jobId: 'job-1' }),
}))

vi.mock('../doctor/ProviderAuthContext', () => ({
  useProviderAuth: () => ({ isAuthenticated: true }),
}))

vi.mock('../../services/capabilityStore', () => ({
  clearCapability: vi.fn(),
  useCapability: () => ({
    token: 'document-capability',
    patientId: 'patient-1',
    purpose: 'document_processing',
    scope: ['documents'],
  }),
}))

type TransitionalArray = 'missing' | null | unknown[]

function statusResponse(
  routingReasons: TransitionalArray,
  extractedFields: TransitionalArray
): ExtractionJobStatusResponse {
  const response: Record<string, unknown> = {
    job_id: 'job-1',
    patient_id: 'patient-1',
    status: 'source_only',
    document_type: 'lab_report',
    provider: 'aws_textract',
    provider_version: 'queries-v1',
    document_confidence: null,
    routing_lane: null,
    candidate_count: 0,
    candidates: [],
    identity_validation: 'passed',
    auto_commit_enabled: false,
    clinician_adjudication_required: true,
    created_at: '2026-08-02T00:00:00Z',
  }
  if (routingReasons !== 'missing') response.routing_reasons = routingReasons
  if (extractedFields !== 'missing') response.extracted_fields = extractedFields
  return response as unknown as ExtractionJobStatusResponse
}

describe('JobStatusScreen transitional response safety', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    push.mockReset()
  })

  it.each([
    ['missing', 'missing', 'missing'],
    ['null', null, null],
    ['empty', [], []],
  ] as const)(
    'renders without throwing when routing reasons and extracted fields are %s',
    async (_label, routingReasons, extractedFields) => {
      vi.spyOn(NexaApiClient, 'getExtractionJobStatus').mockResolvedValue(
        statusResponse(routingReasons, extractedFields)
      )

      renderWithTamagui(<JobStatusScreen />)

      expect(await screen.findByText('Genuine extraction result')).toBeTruthy()
      expect(screen.getByText('Routing: not routed')).toBeTruthy()
      expect(screen.queryByText(/undefined/)).toBeNull()
    }
  )

  it('joins and displays multiple routing reasons without hiding them', async () => {
    const response = statusResponse(['LOW_FIELD_CONFIDENCE', 'SOURCE_TEXT_MISSING'], [])
    response.routing_lane = 'SOURCE_ONLY'
    vi.spyOn(NexaApiClient, 'getExtractionJobStatus').mockResolvedValue(response)

    renderWithTamagui(<JobStatusScreen />)

    expect(
      await screen.findByText('Routing: SOURCE_ONLY · LOW_FIELD_CONFIDENCE, SOURCE_TEXT_MISSING')
    ).toBeTruthy()
  })

  it('uses normalized extracted fields for legacy status counts', async () => {
    vi.spyOn(NexaApiClient, 'getExtractionJobStatus').mockResolvedValue(
      statusResponse([], [{ status: 'auto_approved' }, { status: 'needs_review' }])
    )

    renderWithTamagui(<JobStatusScreen />)

    expect(await screen.findByText('Legacy fields: 1 auto-approved · 1 need review')).toBeTruthy()
  })
})
