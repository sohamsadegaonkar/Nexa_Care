import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithTamagui } from '../../../../test/test-utils'
import { ApiError } from '../../utils/apiClient'
import { PatientSearchScreen } from './PatientSearchScreen'
import * as nfcResolveService from '../../services/nfcResolve'
import * as patientDiscoveryService from '../../services/patientDiscovery'

const push = vi.fn()
const setDiscoverySelection = vi.fn()
let mockSearchParams = new URLSearchParams()

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push }),
  useSearchParams: () => mockSearchParams,
}))

vi.mock('@tamagui/lucide-icons', () => ({
  ArrowRight: () => null,
  CheckCircle: () => null,
  Phone: () => null,
  QrCode: () => null,
  RadioReceiver: () => null,
  Search: () => null,
  ShieldCheck: () => null,
}))

let mockAuthState = {
  isAuthenticated: true,
  session: {
    hospital: { hospital_id: 'hospital-test-1', facility_code: 'H1', display_name: 'City Hospital' },
    provider: {
      provider_id: 'prov-1',
      display_name: 'Dr. Meera',
      medical_registration_number: 'MRN-1',
      specialty: 'General',
      contact_email: 'meera@hospital.test',
      role: 'clinician',
      roles: ['clinician'],
    },
    expires_at: '2099-01-01T00:00:00Z',
  },
  discoverySelection: null as any,
  setDiscoverySelection,
}

vi.mock('./ProviderAuthContext', () => ({
  useProviderAuth: () => mockAuthState,
}))

describe('PatientSearchScreen Clinician UX Contract', () => {
  beforeEach(() => {
    push.mockReset()
    setDiscoverySelection.mockReset()
    vi.restoreAllMocks()
    mockSearchParams = new URLSearchParams()
    mockAuthState.discoverySelection = null
  })

  it('searches by Nexa Patient ID and navigates to request consent', async () => {
    const discoverSpy = vi.spyOn(patientDiscoveryService, 'discoverPatientExact').mockResolvedValue({
      discovery_handle: 'disc-handle-public-1',
      expires_at: '2099-01-01T00:15:00Z',
      patient_id: 'patient-uuid-1',
    })

    renderWithTamagui(<PatientSearchScreen />)

    expect(screen.getByRole('heading', { name: 'Find Patient', level: 1 })).toBeTruthy()
    expect(screen.getByText('Enter Nexa Patient Identifier')).toBeTruthy()

    const idInput = screen.getByPlaceholderText('NC-...')
    fireEvent.change(idInput, { target: { value: 'NC-A1B2C3D4E5F6' } })
    fireEvent.click(screen.getByText('Continue to Request Consent'))

    await waitFor(() => {
      expect(discoverSpy).toHaveBeenCalledWith(
        { identifier_type: 'NEXA_PUBLIC_ID', value: 'NC-A1B2C3D4E5F6' },
        'hospital-test-1'
      )
      expect(setDiscoverySelection).toHaveBeenCalledWith({
        discoveryHandle: 'disc-handle-public-1',
        displayIdentifier: 'NC-A1B2C3D4E5F6',
        expiresAt: '2099-01-01T00:15:00Z',
        source: 'public_id',
      })
      expect(push).toHaveBeenCalledWith('/doctor/request-consent')
    })
  })

  it('displays safe no-match message when lookup fails without leaking account existence', async () => {
    vi.spyOn(patientDiscoveryService, 'discoverPatientExact').mockRejectedValue(
      new ApiError('Not found', 404, 'DISCOVERY_NO_MATCH')
    )

    renderWithTamagui(<PatientSearchScreen />)

    const idInput = screen.getByPlaceholderText('NC-...')
    fireEvent.change(idInput, { target: { value: 'NC-NONEXISTENT' } })
    fireEvent.click(screen.getByText('Continue to Request Consent'))

    await waitFor(() => {
      expect(
        screen.getByText('No patient could be matched with the information provided. Check the details or use another search method.')
      ).toBeTruthy()
    })
  })

  it('switches to Verified Phone mode and performs phone search', async () => {
    const discoverSpy = vi.spyOn(patientDiscoveryService, 'discoverPatientExact').mockResolvedValue({
      discovery_handle: 'disc-handle-phone-1',
      expires_at: '2099-01-01T00:15:00Z',
      patient_id: 'patient-phone-1',
    })

    renderWithTamagui(<PatientSearchScreen />)

    // Switch to Phone mode
    fireEvent.click(screen.getByRole('button', { name: 'Verified Phone' }))
    expect(screen.getByText('Find by Phone')).toBeTruthy()

    const phoneInput = screen.getByPlaceholderText('+91...')
    fireEvent.change(phoneInput, { target: { value: '+919876543210' } })
    fireEvent.click(screen.getByText('Continue to Request Consent'))

    await waitFor(() => {
      expect(discoverSpy).toHaveBeenCalledWith(
        { identifier_type: 'PHONE', value: '+919876543210' },
        'hospital-test-1'
      )
      expect(push).toHaveBeenCalledWith('/doctor/request-consent')
    })
  })

  it('switches to Nexa QR mode and resolves QR payload', async () => {
    const discoverSpy = vi.spyOn(patientDiscoveryService, 'discoverPatientExact').mockResolvedValue({
      discovery_handle: 'disc-handle-qr-1',
      expires_at: '2099-01-01T00:15:00Z',
      patient_id: 'patient-qr-1',
    })

    renderWithTamagui(<PatientSearchScreen />)

    fireEvent.click(screen.getByRole('button', { name: 'Nexa QR' }))
    expect(screen.getByText('Scan Nexa QR')).toBeTruthy()

    const qrInput = screen.getByPlaceholderText('nexa://patient-discovery/v1/NC-...')
    fireEvent.change(qrInput, { target: { value: 'nexa://patient-discovery/v1/NC-TESTQR' } })
    fireEvent.click(screen.getByText('Continue to Request Consent'))

    await waitFor(() => {
      expect(discoverSpy).toHaveBeenCalledWith(
        { identifier_type: 'QR_PUBLIC_ID', value: 'nexa://patient-discovery/v1/NC-TESTQR' },
        'hospital-test-1'
      )
      expect(push).toHaveBeenCalledWith('/doctor/request-consent')
    })
  })

  it('switches to NFC Scan mode and resolves NFC UID', async () => {
    const resolveNfcSpy = vi.spyOn(nfcResolveService, 'resolveNfcCard').mockResolvedValue({
      discovery_handle: 'disc-handle-nfc-1',
      expires_at: '2099-01-01T00:15:00Z',
    })

    renderWithTamagui(<PatientSearchScreen />)

    fireEvent.click(screen.getByRole('button', { name: 'NFC Scan' }))
    expect(screen.getByText('Tap NFC Health Card')).toBeTruthy()

    const nfcInput = screen.getByPlaceholderText('Enter NFC card UID...')
    fireEvent.change(nfcInput, { target: { value: '04A1B2C3D4' } })
    fireEvent.click(screen.getByText('Continue to Request Consent'))

    await waitFor(() => {
      expect(resolveNfcSpy).toHaveBeenCalledWith('04A1B2C3D4')
      expect(push).toHaveBeenCalledWith('/doctor/request-consent')
    })
  })

  it('handles document_upload intent and routes to upload after discovery', async () => {
    mockSearchParams = new URLSearchParams('intent=document_upload')

    vi.spyOn(patientDiscoveryService, 'discoverPatientExact').mockResolvedValue({
      discovery_handle: 'disc-handle-doc-1',
      expires_at: '2099-01-01T00:15:00Z',
      patient_id: 'patient-doc-1',
    })

    renderWithTamagui(<PatientSearchScreen />)

    expect(
      screen.getByText('Identify the patient before requesting document processing and clinical upload consent.')
    ).toBeTruthy()

    const idInput = screen.getByPlaceholderText('NC-...')
    fireEvent.change(idInput, { target: { value: 'NC-DOCPATIENT' } })
    fireEvent.click(screen.getByText('Continue to Request Consent'))

    await waitFor(() => {
      expect(push).toHaveBeenCalledWith('/doctor/request-consent?intent=document_upload')
    })
  })
})
