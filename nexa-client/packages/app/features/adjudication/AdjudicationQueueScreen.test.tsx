import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithTamagui } from '../../../../test/test-utils'
import { clearAllAdjudicationWorkflows } from '../../services/adjudicationWorkflowStore'
import { ApiError, NexaApiClient } from '../../utils/apiClient'
import { ProviderAuthProvider } from '../doctor/ProviderAuthContext'
import { AdjudicationQueueScreen } from './AdjudicationQueueScreen'

const push = vi.fn()
const replace = vi.fn()

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push, replace }),
}))

function renderQueue() {
  return renderWithTamagui(
    <ProviderAuthProvider>
      <AdjudicationQueueScreen />
    </ProviderAuthProvider>
  )
}

describe('adjudication case creation recovery', () => {
  afterEach(clearAllAdjudicationWorkflows)

  beforeEach(() => {
    vi.restoreAllMocks()
    push.mockReset()
    replace.mockReset()
    vi.spyOn(NexaApiClient, 'providerWebSession').mockResolvedValue({
      authenticated: true,
      expires_at: '2099-01-01T00:00:00Z',
      provider_uid: 'provider-1',
      hospital_id: 'hospital-1',
      display_name: 'Provider One',
      hospital_name: 'Hospital One',
      roles: ['admin', 'clinician'],
    })
    vi.spyOn(NexaApiClient, 'listAdjudicationCases').mockResolvedValue([])
  })

  it('reuses the same session and idempotency key after a lost response', async () => {
    const create = vi
      .spyOn(NexaApiClient, 'createAdjudicationCaseFromRoute')
      .mockRejectedValueOnce(new ApiError('network response lost', 0, 'NETWORK_ERROR', true))
      .mockResolvedValueOnce({
        case_id: 'case-1',
        patient_id: 'redacted-by-ui',
        tenant_id: 'tenant-1',
        source_document_id: 'document-1',
        job_id: 'job-1',
        routing_id: 'route-1',
        decision_id: 'decision-1',
        reviewer_id: 'provider-1',
        reviewer_role: 'clinician',
        status: 'PENDING',
        version: 1,
        created_at: '2026-07-29T10:00:00Z',
        resolved_at: null,
        clinical_committed_at: null,
      })
    renderQueue()

    const input = await screen.findByLabelText('Eligible routing reference')
    fireEvent.change(input, { target: { value: 'route-1' } })
    fireEvent.click(screen.getByText('Create field-linked case'))
    expect(await screen.findByText('The adjudication case could not be created.')).toBeTruthy()
    fireEvent.click(screen.getByText('Create field-linked case'))

    await waitFor(() => expect(create).toHaveBeenCalledTimes(2))
    expect(create.mock.calls[1]).toEqual(create.mock.calls[0])
    await waitFor(() =>
      expect(push).toHaveBeenCalledWith('/doctor/pipeline/adjudication/case-1/review')
    )
  })

  it('allows one create request while the action is pending', async () => {
    const pending = new Promise<never>(() => undefined)
    const create = vi
      .spyOn(NexaApiClient, 'createAdjudicationCaseFromRoute')
      .mockReturnValue(pending)
    renderQueue()

    const input = await screen.findByLabelText('Eligible routing reference')
    fireEvent.change(input, { target: { value: 'route-1' } })
    const button = screen.getByText('Create field-linked case')
    fireEvent.click(button)
    fireEvent.click(button)

    expect(create).toHaveBeenCalledTimes(1)
  })

  it('keeps mutation controls hidden from an admin-only provider', async () => {
    vi.mocked(NexaApiClient.providerWebSession).mockResolvedValue({
      authenticated: true,
      expires_at: '2099-01-01T00:00:00Z',
      provider_uid: 'provider-1',
      hospital_id: 'hospital-1',
      display_name: 'Provider One',
      hospital_name: 'Hospital One',
      roles: ['admin'],
    })
    renderQueue()

    expect(
      await screen.findByText(
        'Your role may view operational case status but cannot enter or commit clinical information.'
      )
    ).toBeTruthy()
    expect(screen.queryByText('Create field-linked case')).toBeNull()
  })
})
