import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithTamagui } from '../../../../test/test-utils'
import { NexaApiClient, type ProviderWorkspaceResponse } from '../../utils/apiClient'
import { DoctorDashboardScreen } from './DoctorDashboardScreen'

const push = vi.fn()
const replace = vi.fn()

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push, replace }),
}))

vi.mock('@tamagui/lucide-icons', () => {
  const Mock = () => null
  return {
    FileText: Mock,
    Search: Mock,
    RadioReceiver: Mock,
    ClipboardCheck: Mock,
    ShieldAlert: Mock,
    UserCheck: Mock,
    HeartPulse: Mock,
    Clock: Mock,
    Activity: Mock,
    ArrowUpRight: Mock,
    ShieldCheck: Mock,
    RotateCw: Mock,
  }
})

let authState = {
  hydrated: true,
  isAuthenticated: true,
  displayName: 'Dr. Aarav Patel',
  hospitalName: 'Apollo Care Hospital',
  role: 'clinician',
  treatmentSession: null as any,
}

vi.mock('./ProviderAuthContext', () => ({
  useProviderAuth: () => authState,
}))

describe('DoctorDashboardScreen', () => {
  beforeEach(() => {
    push.mockReset()
    replace.mockReset()
    vi.restoreAllMocks()
    authState = {
      hydrated: true,
      isAuthenticated: true,
      displayName: 'Dr. Aarav Patel',
      hospitalName: 'Apollo Care Hospital',
      role: 'clinician',
      treatmentSession: null,
    }
  })

  it('renders provider trust banner and empty state when no active sessions exist', async () => {
    const mockWorkspace: ProviderWorkspaceResponse = {
      active_sessions: [],
      recent_encounters: [],
      pending_access: [],
      summary_counts: {
        active_sessions_count: 0,
        recent_encounters_count: 0,
        total_patients: 12,
      },
    }
    vi.spyOn(NexaApiClient, 'getProviderWorkspace').mockResolvedValue(mockWorkspace)

    renderWithTamagui(<DoctorDashboardScreen />)

    expect(await screen.findByText('Dr. Aarav Patel')).toBeTruthy()
    expect(screen.getByText('Apollo Care Hospital')).toBeTruthy()
    expect(screen.getByText('Provider session active')).toBeTruthy()
    expect(
      await screen.findByText('No active treatment sessions right now')
    ).toBeTruthy()
  })

  it('renders active treatment sessions and allows continuing consultation when capability is held', async () => {
    authState.treatmentSession = {
      requestId: 'session-uuid-1',
      treatmentToken: 'in-memory-bearer-token',
      allowedOperations: ['CREATE_ENCOUNTER', 'WRITE_VITALS'],
      expiresAt: '2099-01-01T00:00:00Z',
      patientDisplayIdentifier: 'NC-P12345',
      encounterId: 'enc-uuid-1',
    }

    const mockWorkspace: ProviderWorkspaceResponse = {
      active_sessions: [
        {
          session_id: 'session-uuid-1',
          encounter_id: 'enc-uuid-1',
          patient_id: 'patient-uuid-1',
          patient_display_identifier: 'NC-P12345',
          patient_name: 'Priya Sharma',
          status: 'ACTIVE',
          purpose: 'treatment',
          scope: 'treatment',
          allowed_operations: ['CREATE_ENCOUNTER', 'WRITE_VITALS'],
          issued_at: '2026-09-24T10:00:00Z',
          expires_at: '2026-09-24T12:00:00Z',
        },
      ],
      recent_encounters: [],
      pending_access: [],
      summary_counts: {
        active_sessions_count: 1,
        recent_encounters_count: 0,
        total_patients: 15,
      },
    }
    vi.spyOn(NexaApiClient, 'getProviderWorkspace').mockResolvedValue(mockWorkspace)

    renderWithTamagui(<DoctorDashboardScreen />)

    expect(await screen.findByText('Priya Sharma')).toBeTruthy()
    expect(screen.getByText('NC-P12345')).toBeTruthy()
    expect(screen.getByText('Active Treatment Session')).toBeTruthy()

    const continueBtn = await screen.findByRole('button', { name: 'Continue Consultation' })
    expect(continueBtn).toBeTruthy()
    fireEvent.click(continueBtn)
    expect(push).toHaveBeenCalledWith('/doctor/treatment-vitals')
  })

  it('renders Request New Access when active session exists on server but memory capability is absent', async () => {
    authState.treatmentSession = null // No in-memory capability

    const mockWorkspace: ProviderWorkspaceResponse = {
      active_sessions: [
        {
          session_id: 'session-uuid-2',
          encounter_id: null,
          patient_id: 'patient-uuid-2',
          patient_display_identifier: 'NC-P99887',
          patient_name: 'Rahul Verma',
          status: 'ACTIVE',
          purpose: 'treatment',
          scope: 'treatment',
          allowed_operations: ['CREATE_ENCOUNTER', 'WRITE_VITALS'],
          issued_at: '2026-09-24T10:00:00Z',
          expires_at: '2026-09-24T12:00:00Z',
        },
      ],
      recent_encounters: [],
      pending_access: [],
      summary_counts: {
        active_sessions_count: 1,
        recent_encounters_count: 0,
        total_patients: 15,
      },
    }
    vi.spyOn(NexaApiClient, 'getProviderWorkspace').mockResolvedValue(mockWorkspace)

    renderWithTamagui(<DoctorDashboardScreen />)

    expect(await screen.findByText('Rahul Verma')).toBeTruthy()
    const requestAccessBtn = await screen.findByRole('button', { name: 'Request New Access' })
    expect(requestAccessBtn).toBeTruthy()
    fireEvent.click(requestAccessBtn)
    expect(push).toHaveBeenCalledWith('/doctor/patient-search')
  })

  it('renders recent encounters and opens patient workspace on click', async () => {
    const mockWorkspace: ProviderWorkspaceResponse = {
      active_sessions: [],
      recent_encounters: [
        {
          encounter_id: 'encounter-1',
          clinical_session_id: 'session-1',
          patient_id: 'patient-1',
          patient_display_identifier: 'NC-REC001',
          patient_name: 'Anita Desai',
          created_at: '2026-09-24T09:30:00Z',
        },
      ],
      pending_access: [],
      summary_counts: {
        active_sessions_count: 0,
        recent_encounters_count: 1,
        total_patients: 10,
      },
    }
    vi.spyOn(NexaApiClient, 'getProviderWorkspace').mockResolvedValue(mockWorkspace)

    renderWithTamagui(<DoctorDashboardScreen />)

    expect(await screen.findByText('Recent Clinical Encounters')).toBeTruthy()
    expect(screen.getByText('Anita Desai')).toBeTruthy()
    expect(screen.getByText('NC-REC001')).toBeTruthy()

    const openBtn = screen.getByRole('button', { name: 'Open Patient Workspace' })
    fireEvent.click(openBtn)
    expect(push).toHaveBeenCalledWith('/doctor/patient-search')
  })
})
