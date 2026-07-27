import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithTamagui } from '../../../../test/test-utils'
import { ApiError, NexaApiClient } from '../../utils/apiClient'
import { RequestConsentScreen } from './RequestConsentScreen'

const push = vi.fn()
const back = vi.fn()

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push, back }),
  useSearchParams: () => ({ get: (key: string) => (key === 'patient_id' ? 'patient-1' : null) }),
}))

vi.mock('./ProviderAuthContext', () => ({
  useProviderAuth: () => ({
    providerId: 'provider-1',
    hospitalName: 'Nexa Alpha Hospital',
    isAuthenticated: true,
    session: { hospital: { hospital_id: 'hospital-1' } },
  }),
}))

describe('RequestConsentScreen web consent flow', () => {
  beforeEach(() => {
    push.mockReset()
    back.mockReset()
    vi.restoreAllMocks()
  })

  it('renders DOM selects without viewport prop warnings and updates all values', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    const error = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    renderWithTamagui(<RequestConsentScreen />)

    const purpose = screen.getByLabelText('Purpose') as HTMLSelectElement
    const scope = screen.getByLabelText('Requested Scope') as HTMLSelectElement
    const duration = screen.getByLabelText('Access Duration') as HTMLSelectElement
    expect(screen.getAllByRole('combobox')).toHaveLength(3)
    expect(screen.getAllByRole('option').map((option) => option.textContent)).toEqual(
      expect.arrayContaining([
        'Treatment',
        'Diagnostic Review',
        'Clinical',
        'Full Record',
        '5 minutes',
        '30 minutes',
      ])
    )

    fireEvent.change(purpose, { target: { value: 'diagnostic_review' } })
    fireEvent.change(scope, { target: { value: 'full' } })
    fireEvent.change(duration, { target: { value: '1800' } })
    expect(purpose.value).toBe('diagnostic_review')
    expect(scope.value).toBe('full')
    expect(duration.value).toBe('1800')
    expect(screen.getByText('Reviewing test results')).toBeTruthy()
    expect(screen.getByText('Complete patient record access')).toBeTruthy()
    expect(screen.getByText(/Selected: 30 minutes/)).toBeTruthy()
    expect(JSON.stringify([...warn.mock.calls, ...error.mock.calls])).not.toMatch(
      /SelectViewport|non-boolean attribute|elevate|bordered/
    )
  })

  it('submits the controlled contract once with the authenticated hospital context', async () => {
    let resolveRequest!: (value: { request_id: string }) => void
    const pending = new Promise<{ request_id: string }>((resolve) => {
      resolveRequest = resolve
    })
    const requestConsent = vi
      .spyOn(NexaApiClient, 'requestConsent')
      .mockReturnValue(pending as ReturnType<typeof NexaApiClient.requestConsent>)
    renderWithTamagui(<RequestConsentScreen />)

    fireEvent.change(screen.getByLabelText('Purpose'), { target: { value: 'follow_up' } })
    fireEvent.change(screen.getByLabelText('Requested Scope'), { target: { value: 'clinical' } })
    fireEvent.change(screen.getByLabelText('Access Duration'), { target: { value: '3600' } })
    const submit = screen.getByRole('button', { name: 'Request Access' })
    fireEvent.click(submit)
    fireEvent.click(submit)

    expect(requestConsent).toHaveBeenCalledOnce()
    expect(requestConsent).toHaveBeenCalledWith(
      {
        patient_id: 'patient-1',
        provider_id: 'provider-1',
        purpose: 'follow_up',
        scope: 'clinical',
        access_duration_seconds: 3600,
      },
      'hospital-1'
    )
    resolveRequest({ request_id: 'request-1' })
    await waitFor(() =>
      expect(push).toHaveBeenCalledWith('/doctor/waiting?request_id=request-1&patient_id=patient-1')
    )
  })

  it('shows a safe backend validation message and remains on the form', async () => {
    vi.spyOn(NexaApiClient, 'requestConsent').mockRejectedValue(
      new ApiError('Invalid consent scope.', 422, 'VALIDATION_ERROR')
    )
    renderWithTamagui(<RequestConsentScreen />)
    fireEvent.click(screen.getByRole('button', { name: 'Request Access' }))
    expect(await screen.findByText('Consent request failed: Invalid consent scope.')).toBeTruthy()
    expect(push).not.toHaveBeenCalled()
  })
})
