import { fireEvent, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithTamagui } from '../../../../test/test-utils'
import { apiClient } from '../../utils/apiClient'
import AccessHistoryScreen from './AccessHistoryScreen'

const { push } = vi.hoisted(() => ({ push: vi.fn() }))

vi.mock('expo-router', async () => {
  const React = await vi.importActual<typeof import('react')>('react')
  return {
    useRouter: () => ({ push }),
    useFocusEffect: (callback: () => void) => React.useEffect(callback, [callback]),
  }
})

vi.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, right: 0, bottom: 8, left: 0 }),
}))

vi.mock('../../utils/apiClient', () => ({
  apiClient: { get: vi.fn() },
}))

const entry = {
  audit_id: 'audit-1',
  doctor_name: 'Dr. Meera Joshi',
  hospital_name: 'Nexa Hospital',
  purpose: 'treatment',
  accessed_at: new Date(Date.now() - 4 * 60_000).toISOString(),
  data_categories: ['clinical_summary', 'lab_results'],
  is_break_glass: false,
  flag: 'ROUTINE_ACCESS' as const,
}

function expectedExactTimestamp(value: string): string {
  const date = new Date(value)
  const datePart = new Intl.DateTimeFormat('en-IN', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  }).format(date)
  const timePart = new Intl.DateTimeFormat('en-IN', {
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  })
    .format(date)
    .replace(/\b(am|pm)\b/i, (period) => period.toUpperCase())
  return `${datePart} · ${timePart}`
}

describe('AccessHistoryScreen', () => {
  beforeEach(() => {
    vi.mocked(apiClient.get).mockReset()
    push.mockReset()
  })

  it('renders a spinner state during initial loading and keeps Timeline visible', () => {
    vi.mocked(apiClient.get).mockReturnValue(new Promise(() => undefined))
    renderWithTamagui(<AccessHistoryScreen />)

    expect(screen.getByText('Loading access history…')).toBeTruthy()
    expect(screen.getByText('View Health Timeline')).toBeTruthy()
  })

  it('renders the explicit empty state for an empty direct response', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      access_history: [],
      next_cursor: null,
    } as never)
    renderWithTamagui(<AccessHistoryScreen />)

    expect(await screen.findByText('No provider has accessed your records yet.')).toBeTruthy()
    expect(
      screen.getByText('When a provider accesses your data, it will appear here.')
    ).toBeTruthy()
    expect(screen.getByText('View Health Timeline')).toBeTruthy()
  })

  it('renders provider, hospital, humanized purpose, and category chips', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { access_history: [entry], next_cursor: null },
    })
    renderWithTamagui(<AccessHistoryScreen />)

    expect(await screen.findByText('Dr. Meera Joshi')).toBeTruthy()
    expect(screen.getByText('Nexa Hospital')).toBeTruthy()
    expect(screen.getByText('Treatment')).toBeTruthy()
    expect(screen.getByText('Clinical summary')).toBeTruthy()
    expect(screen.getByText('Lab results')).toBeTruthy()
  })

  it('supports a direct response', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      access_history: [entry],
      next_cursor: null,
    } as never)
    renderWithTamagui(<AccessHistoryScreen />)

    expect(await screen.findByText('Dr. Meera Joshi')).toBeTruthy()
  })

  it('renders every received entry through the FlatList', async () => {
    const entries = Array.from({ length: 11 }, (_, index) => ({
      ...entry,
      audit_id: `audit-${index + 1}`,
      doctor_name: `Provider ${index + 1}`,
    }))
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { access_history: entries, next_cursor: null },
    })
    renderWithTamagui(<AccessHistoryScreen />)

    expect(await screen.findByText('Provider 11')).toBeTruthy()
    for (let index = 1; index <= 11; index += 1) {
      expect(screen.getByText(`Provider ${index}`)).toBeTruthy()
    }
  })

  it('renders routine styling, relative time, and exact local time', () => {
    vi.mocked(apiClient.get).mockReturnValue(new Promise(() => undefined))
    renderWithTamagui(<AccessHistoryScreen history={[entry]} />)

    expect(screen.getByText('Routine access')).toBeTruthy()
    expect(screen.getByText('ROUTINE')).toBeTruthy()
    expect(screen.getByText('4 min ago')).toBeTruthy()
    expect(screen.getByText(expectedExactTimestamp(entry.accessed_at))).toBeTruthy()
  })

  it('keeps break-glass access visibly distinct', () => {
    vi.mocked(apiClient.get).mockReturnValue(new Promise(() => undefined))
    renderWithTamagui(
      <AccessHistoryScreen
        history={[
          {
            ...entry,
            audit_id: 'audit-emergency',
            purpose: 'emergency_care',
            is_break_glass: true,
            flag: 'BREAK_GLASS_ACCESS',
          },
        ]}
      />
    )

    expect(screen.getByText('Emergency access')).toBeTruthy()
    expect(screen.getByText('EMERGENCY ACCESS')).toBeTruthy()
    expect(screen.getByText('Emergency care')).toBeTruthy()
  })

  it('shows Time unavailable for a malformed timestamp', () => {
    vi.mocked(apiClient.get).mockReturnValue(new Promise(() => undefined))
    renderWithTamagui(
      <AccessHistoryScreen history={[{ ...entry, accessed_at: 'not-a-timestamp' }]} />
    )

    expect(screen.getAllByText('Time unavailable')).toHaveLength(2)
  })

  it('keeps repeated legitimate accesses with distinct audit IDs visible', () => {
    vi.mocked(apiClient.get).mockReturnValue(new Promise(() => undefined))
    renderWithTamagui(
      <AccessHistoryScreen
        history={[
          entry,
          {
            ...entry,
            audit_id: 'audit-2',
            accessed_at: new Date(Date.now() - 8 * 60_000).toISOString(),
          },
        ]}
      />
    )

    expect(screen.getAllByText('Dr. Meera Joshi')).toHaveLength(2)
    expect(screen.getByText('4 min ago')).toBeTruthy()
    expect(screen.getByText('8 min ago')).toBeTruthy()
  })

  it('does not render temporary diagnostic labels', () => {
    vi.mocked(apiClient.get).mockReturnValue(new Promise(() => undefined))
    renderWithTamagui(<AccessHistoryScreen history={[entry]} />)

    expect(screen.queryByText(/AH-FIX-02/)).toBeNull()
    expect(screen.queryByText(/Access record \d+/)).toBeNull()
  })

  it('preserves existing entries while focus refresh is in flight', () => {
    vi.mocked(apiClient.get).mockReturnValue(new Promise(() => undefined))
    renderWithTamagui(<AccessHistoryScreen history={[entry]} />)

    expect(screen.getByText('Dr. Meera Joshi')).toBeTruthy()
    expect(screen.queryByText('Loading access history…')).toBeNull()
  })

  it('preserves entries and shows a retry warning when refresh fails', async () => {
    vi.mocked(apiClient.get).mockRejectedValue(new Error('offline'))
    renderWithTamagui(<AccessHistoryScreen history={[entry]} />)

    expect(await screen.findByText('Failed to load access history.')).toBeTruthy()
    expect(screen.getByText('Dr. Meera Joshi')).toBeTruthy()
    expect(screen.getByText('Retry')).toBeTruthy()
  })

  it('shows Retry when the initial request times out', async () => {
    vi.mocked(apiClient.get).mockRejectedValue(
      Object.assign(new Error('timed out'), { code: 'REQUEST_TIMEOUT' })
    )
    renderWithTamagui(<AccessHistoryScreen />)

    expect(await screen.findByText('Failed to load access history.')).toBeTruthy()
    expect(screen.getByText('Retry')).toBeTruthy()
  })

  it('shows an explicit error for a malformed response', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: { next_cursor: null } } as never)
    renderWithTamagui(<AccessHistoryScreen />)

    expect(await screen.findByText('Access history returned an invalid response.')).toBeTruthy()
  })

  it('rejects an entry without a usable audit ID', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { access_history: [{ ...entry, audit_id: '   ' }], next_cursor: null },
    })
    renderWithTamagui(<AccessHistoryScreen />)

    expect(await screen.findByText('Access history returned an invalid response.')).toBeTruthy()
    expect(screen.queryByText('Dr. Meera Joshi')).toBeNull()
  })

  it('appends older records and deduplicates by audit_id', async () => {
    const older = {
      ...entry,
      audit_id: 'audit-2',
      doctor_name: 'Dr. Older Record',
    }
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({
        data: { access_history: [entry], next_cursor: 'cursor-1' },
      })
      .mockResolvedValueOnce({
        data: { access_history: [entry, older], next_cursor: null },
      })
    renderWithTamagui(<AccessHistoryScreen />)

    fireEvent.click(await screen.findByText('Load older records'))
    expect(await screen.findByText('Dr. Older Record')).toBeTruthy()
    expect(screen.getAllByText('Dr. Meera Joshi')).toHaveLength(1)
  })
})
