import { fireEvent, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithTamagui } from '../../../../test/test-utils'
import { discoverPatientExact } from '../../services/patientDiscovery'
import { NexaApiClient } from '../../utils/apiClient'
import { EmergencyAccessScreen } from './EmergencyAccessScreen'

const push = vi.fn()
const setAccessGrant = vi.fn()

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push }),
}))

vi.mock('@tamagui/lucide-icons', () => ({
  AlertTriangle: () => null,
  ChevronDown: () => null,
  Phone: () => null,
  QrCode: () => null,
  RadioReceiver: () => null,
  Search: () => null,
}))

vi.mock('../../services/patientDiscovery', () => ({
  discoverPatientExact: vi.fn(),
}))

vi.mock('./ProviderAuthContext', () => ({
  useProviderAuth: () => ({
    isAuthenticated: true,
    session: { hospital: { hospital_id: 'hospital-1' } },
    setAccessGrant,
  }),
}))

describe('EmergencyAccessScreen navigation', () => {
  beforeEach(() => {
    push.mockReset()
    setAccessGrant.mockReset()
    vi.restoreAllMocks()
    vi.mocked(discoverPatientExact).mockResolvedValue({
      discovery_handle: 'discovery-capability',
      expires_at: '2099-01-01T00:00:00Z',
    })
  })

  it('discovers the patient and uses the server-resolved identity without putting it in the URL', async () => {
    const discoveredIssue = vi.spyOn(NexaApiClient, 'breakGlassDiscoveredIssue').mockResolvedValue({
      patient_id: 'server-resolved-patient',
      authorization_ref: 'emergency-reference',
      consent_token: 'emergency-capability',
      expires_at: '2099-01-01T00:00:00Z',
      approved_scope: ['clinical'],
      policy_version: 'synthetic-policy',
    })
    const legacyIssue = vi.spyOn(NexaApiClient, 'breakGlassIssue')

    renderWithTamagui(<EmergencyAccessScreen />)

    fireEvent.change(screen.getByPlaceholderText('Nexa Patient ID'), {
      target: { value: 'NC-DEMO' },
    })
    fireEvent.click(screen.getByText('Identify Patient'))

    expect(await screen.findByText('Patient identified using Nexa Patient ID')).toBeTruthy()

    fireEvent.change(
      screen.getByPlaceholderText('Describe why emergency access is clinically necessary'),
      {
        target: { value: 'Immediate assessment is necessary for a life-threatening condition.' },
      }
    )
    fireEvent.click(screen.getByText('Request Emergency Access'))

    await vi.waitFor(() => expect(push).toHaveBeenCalledWith('/doctor/patient-record'))
    expect(discoverPatientExact).toHaveBeenCalledWith(
      { identifier_type: 'NEXA_PUBLIC_ID', value: 'NC-DEMO' },
      'hospital-1'
    )
    expect(discoveredIssue).toHaveBeenCalledWith(
      expect.objectContaining({
        discovery_handle: 'discovery-capability',
        justification: 'Immediate assessment is necessary for a life-threatening condition.',
      })
    )
    expect(legacyIssue).not.toHaveBeenCalled()
    expect(setAccessGrant).toHaveBeenCalledWith(
      expect.objectContaining({ patientId: 'server-resolved-patient' })
    )
    expect(push.mock.calls.flat().join(' ')).not.toContain('server-resolved-patient')
  })
})
