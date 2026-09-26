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

describe('AdjudicationQueueScreen Clinician Review Inbox', () => {
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

  it('renders clinical review inbox and navigates to case review', async () => {
    vi.spyOn(NexaApiClient, 'listAdjudicationCases').mockResolvedValue([
      {
        case_id: 'case-1',
        patient_id: 'NC-PATIENT-1234',
        tenant_id: 'tenant-1',
        source_document_id: 'blood-panel-report.pdf',
        job_id: 'secret-job-1',
        routing_id: 'secret-route-1',
        decision_id: 'secret-decision-1',
        reviewer_id: 'provider-1',
        reviewer_role: 'clinician',
        status: 'PENDING',
        version: 1,
        created_at: '2026-07-29T10:00:00Z',
        resolved_at: null,
        clinical_committed_at: null,
      },
    ])
    renderQueue()

    expect(await screen.findByText('Review Imported Records')).toBeTruthy()
    expect(await screen.findByText('Lab Report')).toBeTruthy()
    expect(screen.getByText('Needs Clinical Verification')).toBeTruthy()
    expect(screen.getByText('Patient: NC-PATIENT-1234')).toBeTruthy()

    // Assert engineering input fields are absent
    expect(screen.queryByLabelText('Eligible routing reference')).toBeNull()
    expect(screen.queryByText('Create field-linked case')).toBeNull()
    expect(screen.queryByText('secret-job-1')).toBeNull()
    expect(screen.queryByText('secret-route-1')).toBeNull()

    fireEvent.click(screen.getByText('Review Document'))
    expect(push).toHaveBeenCalledWith('/doctor/pipeline/adjudication/case-1/review')
  })

  it('renders empty inbox state with navigation to Documents workspace', async () => {
    vi.spyOn(NexaApiClient, 'listAdjudicationCases').mockResolvedValue([])
    renderQueue()

    expect(
      await screen.findByText('No records currently need clinical review')
    ).toBeTruthy()

    fireEvent.click(screen.getByText('Open Documents Workspace'))
    expect(push).toHaveBeenCalledWith('/doctor/documents')
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

  it('shows error notice when session expires', async () => {
    vi.spyOn(NexaApiClient, 'listAdjudicationCases').mockRejectedValue(
      new ApiError('Access expired', 403, 'ADJUDICATION_CONSENT_INACTIVE')
    )
    renderQueue()

    expect(
      await screen.findByText('Your access to these records has expired. Request access again.')
    ).toBeTruthy()
  })
})
