import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithTamagui } from '../../../../test/test-utils'
import { NexaApiClient } from '../../utils/apiClient'
import { DocumentsWorkspaceScreen } from './DocumentsWorkspaceScreen'

const push = vi.fn()
const replace = vi.fn()
let mockSearchParams = new URLSearchParams()

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push, replace }),
  useSearchParams: () => mockSearchParams,
}))

vi.mock('@tamagui/lucide-icons', () => ({
  AlertCircle: () => null,
  ArrowRight: () => null,
  CheckCircle: () => null,
  Clock: () => null,
  FilePlus: () => null,
  FileText: () => null,
  Filter: () => null,
}))

let mockAuthState = {
  hydrated: true,
  isAuthenticated: true,
  discoverySelection: null as any,
  accessGrant: null as any,
}

vi.mock('./ProviderAuthContext', () => ({
  useProviderAuth: () => mockAuthState,
}))

describe('DocumentsWorkspaceScreen Clinician UX Contract', () => {
  beforeEach(() => {
    push.mockReset()
    replace.mockReset()
    vi.restoreAllMocks()
    mockSearchParams = new URLSearchParams()
    mockAuthState.discoverySelection = null
    mockAuthState.accessGrant = null
  })

  it('renders empty documents state when no external records exist', async () => {
    vi.spyOn(NexaApiClient, 'listAdjudicationCases').mockResolvedValue([])

    renderWithTamagui(<DocumentsWorkspaceScreen />)

    await waitFor(() => {
      expect(screen.getByText('No external documents found')).toBeTruthy()
      expect(screen.getAllByText('+ Add Patient Document').length).toBeGreaterThan(0)
    })
  })

  it('renders imported records with clinician status and navigates to review', async () => {
    vi.spyOn(NexaApiClient, 'listAdjudicationCases').mockResolvedValue([
      {
        case_id: 'case-test-101',
        patient_id: 'NC-PATIENT-1234',
        tenant_id: 'tenant-1',
        source_document_id: 'blood-test-panel.pdf',
        job_id: 'internal-job-secret-999',
        routing_id: 'internal-route-secret-888',
        decision_id: 'internal-decision-secret-777',
        reviewer_id: 'prov-1',
        reviewer_role: 'clinician',
        status: 'PENDING',
        version: 1,
        created_at: '2026-09-20T10:00:00Z',
        resolved_at: null,
        clinical_committed_at: null,
      },
    ])

    renderWithTamagui(<DocumentsWorkspaceScreen />)

    await waitFor(() => {
      expect(screen.getByText('NC-PATIENT-1234')).toBeTruthy()
      expect(screen.getByText('Lab Report')).toBeTruthy()
      expect(screen.getByText('Needs clinical verification')).toBeTruthy()
      expect(screen.getByText('Review Document')).toBeTruthy()
    })

    // Assert internal pipeline/job/routing codes are NOT displayed to the doctor
    expect(screen.queryByText('internal-job-secret-999')).toBeNull()
    expect(screen.queryByText('internal-route-secret-888')).toBeNull()
    expect(screen.queryByText('internal-decision-secret-777')).toBeNull()

    fireEvent.click(screen.getByText('Review Document'))
    expect(push).toHaveBeenCalledWith('/doctor/pipeline/adjudication/case-test-101/review')
  })

  it('routes to patient search for document upload when no patient is preselected', async () => {
    vi.spyOn(NexaApiClient, 'listAdjudicationCases').mockResolvedValue([])

    renderWithTamagui(<DocumentsWorkspaceScreen />)

    await waitFor(() => {
      expect(screen.getAllByText('+ Add Patient Document').length).toBeGreaterThan(0)
    })

    fireEvent.click(screen.getAllByText('+ Add Patient Document')[0])
    expect(push).toHaveBeenCalledWith('/doctor/patient-search?intent=document_upload')
  })

  it('routes directly to upload when patient context already exists in memory', async () => {
    mockAuthState.accessGrant = {
      patientId: 'patient-active-uuid',
      consentToken: 'active-token',
      requestId: 'req-1',
      purpose: 'CARE',
      scope: 'full',
      expiresAt: '2099-01-01T00:00:00Z',
    }

    vi.spyOn(NexaApiClient, 'listAdjudicationCases').mockResolvedValue([])

    renderWithTamagui(<DocumentsWorkspaceScreen />)

    await waitFor(() => {
      expect(screen.getAllByText('+ Add Patient Document').length).toBeGreaterThan(0)
    })

    fireEvent.click(screen.getAllByText('+ Add Patient Document')[0])
    // Should NOT route to patient-search! Directly opens upload with existing patient context
    expect(push).toHaveBeenCalledWith('/doctor/pipeline/upload')
  })
})
