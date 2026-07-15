import { act, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithTamagui } from '../../../../test/test-utils'
import { NexaApiClient } from '../../utils/apiClient'
import { PatientRecordViewerScreen } from './PatientRecordViewerScreen'

const push = vi.fn()
const clearAccessGrant = vi.fn()
let accessGrant: any = null

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push }),
  useSearchParams: () => ({ get: (key: string) => key === 'patient_id' ? 'patient-1' : null }),
}))

vi.mock('@tamagui/lucide-icons', () => {
  const Icon = () => null
  return {
    AlertTriangle: Icon, Clock: Icon, ShieldCheck: Icon, FileText: Icon,
    Heart: Icon, Pill: Icon, FlaskConical: Icon, AlertOctagon: Icon, Activity: Icon,
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
    await act(async () => { await Promise.resolve() })
    expect(await screen.findByText('Access Error')).toBeTruthy()
    expect(summary).not.toHaveBeenCalled()
  })

  it('uses the claimed patient, capability, and hospital only in headers', async () => {
    accessGrant = {
      requestId: 'request-1', patientId: 'patient-1', consentToken: 'capability-secret',
      purpose: 'treatment', scope: 'clinical', expiresAt: '2099-01-01T00:00:00Z',
    }
    const summary = vi.spyOn(NexaApiClient, 'getPatientSummary').mockResolvedValue({
      patient_id: 'patient-1', pii: { patient_name: 'Patient', phone: '', aadhaar_abha_id: '' },
      clinical_summary: { blood_group: '', allergies: [], chronic_conditions: [], active_medications: [] },
      shard_scope: 'clinical',
    })
    vi.spyOn(NexaApiClient, 'getPatientTimeline').mockResolvedValue({ patient_id: 'patient-1', events: [], next_cursor: null })
    renderWithTamagui(<PatientRecordViewerScreen />)
    await act(async () => { await Promise.resolve(); await Promise.resolve() })
    expect(summary).toHaveBeenCalledWith('patient-1', 'capability-secret', 'hospital-1', 'clinical_summary')
    expect(await screen.findByText('Patient Record')).toBeTruthy()
  })
})
