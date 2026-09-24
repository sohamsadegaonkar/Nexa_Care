import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithTamagui } from '../../../../test/test-utils'
import { NexaApiClient, type DashboardMetrics } from '../../utils/apiClient'
import { DashboardScreen } from './DashboardScreen'

const push = vi.fn()
const replace = vi.fn()

vi.mock('solito/navigation', () => ({
  useRouter: () => ({ push, replace }),
}))

describe('DashboardScreen', () => {
  beforeEach(() => {
    push.mockReset()
    replace.mockReset()
    vi.restoreAllMocks()
  })

  it('renders all four canonical backend metrics without invented fields', async () => {
    const mockMetrics: DashboardMetrics = {
      total_patients: 120,
      active_consents: 45,
      break_glass_grants: 3,
      review_backlog: 7,
      definitions_version: '2026-07-17',
    }
    vi.spyOn(NexaApiClient, 'getDashboardMetrics').mockResolvedValue(mockMetrics)

    renderWithTamagui(<DashboardScreen />)

    expect(await screen.findByText('Provider Dashboard')).toBeTruthy()
    expect(screen.getByText('Total Patients')).toBeTruthy()
    expect(screen.getByText('120')).toBeTruthy()

    expect(screen.getByText('Active Consents')).toBeTruthy()
    expect(screen.getByText('45')).toBeTruthy()

    expect(screen.getByText('Break-Glass Grants')).toBeTruthy()
    expect(screen.getByText('3')).toBeTruthy()

    expect(screen.getByText('Review Backlog')).toBeTruthy()
    expect(screen.getByText('7')).toBeTruthy()

    // Assert that invented fields are strictly absent
    expect(screen.queryByText('Avg Duration')).toBeNull()
    expect(screen.queryByText('Revisit Rate')).toBeNull()
    expect(screen.queryByText('Productivity')).toBeNull()
  })

  it('renders error state and retry button when metrics call fails', async () => {
    vi.spyOn(NexaApiClient, 'getDashboardMetrics').mockRejectedValue(new Error('Network error'))

    renderWithTamagui(<DashboardScreen />)

    expect(await screen.findByText('Dashboard unavailable')).toBeTruthy()
    expect(screen.getByText('Retry')).toBeTruthy()
  })
})
