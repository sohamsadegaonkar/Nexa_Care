import { fireEvent, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithTamagui } from '../../../../test/test-utils'
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
}))

vi.mock('./ProviderAuthContext', () => ({
  useProviderAuth: () => ({ isAuthenticated: true, setAccessGrant }),
}))

describe('EmergencyAccessScreen navigation', () => {
  beforeEach(() => {
    push.mockReset()
    setAccessGrant.mockReset()
    vi.restoreAllMocks()
  })

  it('keeps the authorized patient identifier out of the record URL', async () => {
    vi.spyOn(NexaApiClient, 'breakGlassIssue').mockResolvedValue({
      authorization_ref: 'emergency-reference',
      consent_token: 'emergency-capability',
      expires_at: '2099-01-01T00:00:00Z',
      approved_scope: ['clinical'],
      policy_version: 'synthetic-policy',
    })
    renderWithTamagui(<EmergencyAccessScreen />)

    fireEvent.change(screen.getByPlaceholderText('Canonical patient UUID'), {
      target: { value: 'patient-verified' },
    })
    fireEvent.change(screen.getByPlaceholderText('Clinical justification'), {
      target: { value: 'Immediate assessment is necessary for a life-threatening condition.' },
    })
    fireEvent.click(screen.getByText('Issue minimum-necessary emergency access'))

    await vi.waitFor(() => expect(push).toHaveBeenCalledWith('/doctor/patient-record'))
    expect(push.mock.calls.flat().join(' ')).not.toContain('patient-verified')
  })
})
