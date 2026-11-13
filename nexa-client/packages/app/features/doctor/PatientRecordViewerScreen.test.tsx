import { act, fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithTamagui } from '../../../../test/test-utils'
import { ApiError, NexaApiClient } from '../../utils/apiClient'
import { PatientRecordViewerScreen } from './PatientRecordViewerScreen'

const push = vi.fn()
const clearAccessGrant = vi.fn()
let accessGrant: any = null

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push }),
}))

vi.mock('@tamagui/lucide-icons', () => {
  const Icon = () => null
  return {
    AlertTriangle: Icon,
    Clock: Icon,
    ShieldCheck: Icon,
    FileText: Icon,
    Heart: Icon,
    Pill: Icon,
    FlaskConical: Icon,
    AlertOctagon: Icon,
    Activity: Icon,
  }
})

vi.mock('./ProviderAuthContext', () => ({
  useProviderAuth: () => ({
    providerId: 'provider-1',
    isAuthenticated: true,
    session: { hospital: { hospital_id: 'hospital-1' } },
    accessGrant,
    clearAccessGrant,
  }),
}))

describe('PatientRecordViewerScreen capability handoff', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    push.mockReset()
    clearAccessGrant.mockReset()
    accessGrant = null
  })

  it('fails safely after refresh when the in-memory capability is absent', async () => {
    const summary = vi.spyOn(NexaApiClient, 'getPatientSummary')
    renderWithTamagui(<PatientRecordViewerScreen />)
    await act(async () => {
      await Promise.resolve()
    })
    expect(await screen.findByText('Access Error')).toBeTruthy()
    expect(summary).not.toHaveBeenCalled()
  })

  it('uses the claimed patient, capability, and hospital from memory-only access state', async () => {
    accessGrant = {
      requestId: 'request-1',
      patientId: 'patient-1',
      consentToken: 'capability-secret',
      purpose: 'treatment',
      scope: 'clinical',
      expiresAt: '2099-01-01T00:00:00Z',
    }
    const summary = vi.spyOn(NexaApiClient, 'getPatientSummary').mockResolvedValue({
      patient_id: 'patient-1',
      pii: { patient_name: 'Patient', phone: '', aadhaar_abha_id: '' },
      clinical_summary: {
        blood_group: '',
        allergies: [],
        chronic_conditions: [],
        active_medications: [],
      },
      shard_scope: 'clinical',
    })
    vi.spyOn(NexaApiClient, 'getPatientTimeline').mockResolvedValue({
      patient_id: 'patient-1',
      events: [],
      next_cursor: null,
    })
    renderWithTamagui(<PatientRecordViewerScreen />)
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(summary).toHaveBeenCalledWith(
      'patient-1',
      'capability-secret',
      'hospital-1',
      'clinical_summary'
    )
    expect(await screen.findByText('Patient Record')).toBeTruthy()
  })
  it('retains an active capability after a transient record validation failure', async () => {
    accessGrant = {
      requestId: 'request-1',
      patientId: 'patient-1',
      consentToken: 'capability-secret',
      purpose: 'treatment',
      scope: 'clinical',
      expiresAt: '2099-01-01T00:00:00Z',
    }

    vi.spyOn(NexaApiClient, 'getPatientSummary').mockRejectedValue(
      new ApiError('Network interrupted', 0, 'NETWORK_ERROR', true)
    )

    renderWithTamagui(<PatientRecordViewerScreen />)

    expect(await screen.findByText('Access Error')).toBeTruthy()
    expect(await screen.findByText('Retry Secure Validation')).toBeTruthy()
    expect(clearAccessGrant).not.toHaveBeenCalled()
  })

  it('clears the capability after a terminal server-side consent rejection', async () => {
    accessGrant = {
      requestId: 'request-1',
      patientId: 'patient-1',
      consentToken: 'capability-secret',
      purpose: 'treatment',
      scope: 'clinical',
      expiresAt: '2099-01-01T00:00:00Z',
    }

    vi.spyOn(NexaApiClient, 'getPatientSummary').mockRejectedValue(
      new ApiError('Consent expired', 403, 'FORBIDDEN', false)
    )

    renderWithTamagui(<PatientRecordViewerScreen />)

    expect(await screen.findByText('Access Error')).toBeTruthy()
    expect(clearAccessGrant).toHaveBeenCalled()
  })
  it.each(['clinical', 'full'])(
    'shows a sourced allergy warning and fetches timeline for V3 %s read scope',
    async (scope) => {
      accessGrant = {
        requestId: 'request-1',
        patientId: 'patient-1',
        consentToken: 'capability-secret',
        purpose: 'treatment',
        scope,
        expiresAt: '2099-01-01T00:00:00Z',
      }
      vi.spyOn(NexaApiClient, 'getPatientSummary').mockResolvedValue({
        patient_id: 'patient-1',
        pii: { patient_name: '[REDACTED]', phone: '', aadhaar_abha_id: '' },
        clinical_summary: {
          blood_group: '',
          allergies: ['Penicillin (severe)'],
          chronic_conditions: [],
          active_medications: [],
        },
        shard_scope: 'clinical',
      })
      const timeline = vi.spyOn(NexaApiClient, 'getPatientTimeline').mockResolvedValue({
        patient_id: 'patient-1',
        events: [{
          event_id: 'event-1',
          event_type: 'ALLERGY',
          title: 'Allergy observation',
          summary: 'Documented allergy',
          event_date: '2026-09-18T12:00:00Z',
          source: 'manual',
        }],
        next_cursor: null,
      } as any)

      renderWithTamagui(<PatientRecordViewerScreen />)
      expect(await screen.findByText('ALLERGIES: Penicillin (severe)')).toBeTruthy()
      await waitFor(() => {
        expect(timeline).toHaveBeenCalledWith(
          'patient-1',
          'capability-secret',
          'hospital-1'
        )
      })
      fireEvent.click(screen.getByText('Timeline'))
      expect(await screen.findByText('Allergy observation')).toBeTruthy()
    }
  )

  it('treats absent allergy entries as unknown, never confirmed allergy-free', async () => {
    accessGrant = {
      requestId: 'request-1', patientId: 'patient-1',
      consentToken: 'capability-secret', purpose: 'treatment',
      scope: 'clinical', expiresAt: '2099-01-01T00:00:00Z',
    }
    vi.spyOn(NexaApiClient, 'getPatientSummary').mockResolvedValue({
      patient_id: 'patient-1',
      pii: { patient_name: '[REDACTED]', phone: '', aadhaar_abha_id: '' },
      clinical_summary: {
        blood_group: '', allergies: [], chronic_conditions: [], active_medications: [],
      },
      shard_scope: 'clinical',
    })
    vi.spyOn(NexaApiClient, 'getPatientTimeline').mockResolvedValue({
      patient_id: 'patient-1', events: [], next_cursor: null,
    })
    renderWithTamagui(<PatientRecordViewerScreen />)
    await screen.findByText('Patient Record')
    fireEvent.click(screen.getByText('Allergies'))
    expect(await screen.findByText('No allergy information recorded in available Nexa Care records.')).toBeTruthy()
    expect(screen.queryByText('No known allergies.')).toBeNull()
  })

  it('does not present a failed history request as an empty clinical timeline', async () => {
    accessGrant = {
      requestId: 'request-1', patientId: 'patient-1',
      consentToken: 'capability-secret', purpose: 'treatment',
      scope: 'clinical', expiresAt: '2099-01-01T00:00:00Z',
    }
    vi.spyOn(NexaApiClient, 'getPatientSummary').mockResolvedValue({
      patient_id: 'patient-1',
      pii: { patient_name: '[REDACTED]', phone: '', aadhaar_abha_id: '' },
      clinical_summary: {
        blood_group: '', allergies: [], chronic_conditions: [], active_medications: [],
      },
      shard_scope: 'clinical',
    })
    vi.spyOn(NexaApiClient, 'getPatientTimeline').mockRejectedValue(new Error('network unavailable'))
    renderWithTamagui(<PatientRecordViewerScreen />)
    await screen.findByText('Patient Record')
    fireEvent.click(screen.getByText('Timeline'))
    expect(await screen.findByText(/Clinical timeline could not be loaded/)).toBeTruthy()
    expect(screen.queryByText('No timeline events recorded in the available history.')).toBeNull()
  })

  it('never displays a hardcoded or inferred verified document for a patient', async () => {
    accessGrant = {
      requestId: 'request-1', patientId: 'patient-1',
      consentToken: 'capability-secret', purpose: 'treatment',
      scope: 'full', expiresAt: '2099-01-01T00:00:00Z',
    }
    vi.spyOn(NexaApiClient, 'getPatientSummary').mockResolvedValue({
      patient_id: 'patient-1',
      pii: { patient_name: 'Patient', phone: '', aadhaar_abha_id: '' },
      clinical_summary: {
        blood_group: '', allergies: [], chronic_conditions: [], active_medications: [],
      },
      shard_scope: 'full',
    })
    vi.spyOn(NexaApiClient, 'getPatientTimeline').mockResolvedValue({
      patient_id: 'patient-1', events: [], next_cursor: null,
    })
    renderWithTamagui(<PatientRecordViewerScreen />)
    await screen.findByText('Patient Record')
    fireEvent.click(screen.getByText('Documents'))
    expect(await screen.findByText(/Patient-specific documents are not available in this view yet/)).toBeTruthy()
    expect(screen.queryByText('External Health Summary & Diagnostic Report')).toBeNull()
    expect(screen.queryByText('Clinician verified')).toBeNull()
    expect(screen.queryByText('+ Add External Record')).toBeNull()
  })

})
