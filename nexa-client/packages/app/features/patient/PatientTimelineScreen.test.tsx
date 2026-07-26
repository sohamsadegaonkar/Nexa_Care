import React from 'react'
import { fireEvent, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithTamagui } from '../../../../test/test-utils'
import { apiClient } from '../../utils/apiClient'
import PatientTimelineScreen from './PatientTimelineScreen'

const { push } = vi.hoisted(() => ({ push: vi.fn() }))

vi.mock('expo-router', () => ({
  useRouter: () => ({ push }),
}))

vi.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, right: 0, bottom: 8, left: 0 }),
}))

vi.mock('../../utils/apiClient', () => ({
  apiClient: { get: vi.fn() },
}))

vi.mock('react-native', async () => {
  const actual = await vi.importActual<typeof import('react-native')>('react-native')
  return {
    ...actual,
    RefreshControl: ({ onRefresh }: { onRefresh: () => void }) => (
      <button
        type="button"
        onClick={onRefresh}
      >
        Pull to refresh
      </button>
    ),
    SectionList: ({
      sections,
      renderItem,
      renderSectionHeader,
      ListHeaderComponent,
      ListEmptyComponent,
      refreshControl,
    }: any) => (
      <div data-testid="section-list">
        {refreshControl}
        {ListHeaderComponent}
        {sections.length === 0
          ? ListEmptyComponent
          : sections.map((section: any) => (
              <React.Fragment key={section.title}>
                {renderSectionHeader({ section })}
                {section.data.map((item: any, index: number) => (
                  <React.Fragment key={item.event_id}>
                    {renderItem({ item, index, section })}
                  </React.Fragment>
                ))}
              </React.Fragment>
            ))}
      </div>
    ),
  }
})

const event = {
  event_id: 'event-1',
  event_type: 'LAB_RESULT',
  title: 'HbA1c result',
  summary: 'Reviewed laboratory result',
  occurred_at: '2026-07-27T10:00:00Z',
  source: 'ai_extracted',
  source_display: 'Clinician reviewed',
  confidence: 0.96,
  risk_level: 'LOW_RISK',
}

describe('PatientTimelineScreen', () => {
  beforeEach(() => {
    vi.mocked(apiClient.get).mockReset()
    push.mockReset()
  })

  it('renders the initial loading state', () => {
    vi.mocked(apiClient.get).mockReturnValue(new Promise(() => undefined))
    renderWithTamagui(<PatientTimelineScreen />)

    expect(screen.getByText('Loading timeline…')).toBeTruthy()
    expect(screen.getByRole('progressbar')).toBeTruthy()
  })

  it('supports a direct API response and renders event cards', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      events: [event],
      next_cursor: null,
    } as never)
    renderWithTamagui(<PatientTimelineScreen />)

    expect(await screen.findByText('HbA1c result')).toBeTruthy()
    expect(screen.getByText('Reviewed laboratory result')).toBeTruthy()
    expect(screen.getByText('Clinician reviewed')).toBeTruthy()
  })

  it('supports a wrapped API response', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { events: [event], next_cursor: null },
    })
    renderWithTamagui(<PatientTimelineScreen />)

    expect(await screen.findByText('HbA1c result')).toBeTruthy()
  })

  it('renders an explicit malformed-response error', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: { next_cursor: null } } as never)
    renderWithTamagui(<PatientTimelineScreen />)

    expect(await screen.findByText('Health timeline returned an invalid response.')).toBeTruthy()
  })

  it('renders an explicit empty state', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      events: [],
      next_cursor: null,
    } as never)
    renderWithTamagui(<PatientTimelineScreen />)

    expect(await screen.findByText('No clinical events yet.')).toBeTruthy()
    expect(
      screen.getByText('When your provider adds clinical information, it will appear here.')
    ).toBeTruthy()
  })

  it('renders multiple date sections', () => {
    renderWithTamagui(
      <PatientTimelineScreen
        timeline={[
          event,
          {
            ...event,
            event_id: 'event-2',
            title: 'Second event',
            occurred_at: '2026-07-26T10:00:00Z',
          },
        ]}
      />
    )

    expect(screen.getByText(/27 Jul 2026/)).toBeTruthy()
    expect(screen.getByText(/26 Jul 2026/)).toBeTruthy()
  })

  it('uses Date unavailable instead of Invalid Date', () => {
    renderWithTamagui(
      <PatientTimelineScreen
        timeline={[{ ...event, occurred_at: 'not-a-date', event_date: 'also-invalid' }]}
      />
    )

    expect(screen.getByText('Date unavailable')).toBeTruthy()
    expect(screen.queryByText('Invalid Date')).toBeNull()
  })

  it('preserves existing cards while refresh is running', () => {
    vi.mocked(apiClient.get).mockReturnValue(new Promise(() => undefined))
    renderWithTamagui(<PatientTimelineScreen timeline={[event]} />)

    fireEvent.click(screen.getByText('Pull to refresh'))
    expect(screen.getByText('HbA1c result')).toBeTruthy()
  })

  it('preserves cards and shows a warning when refresh fails', async () => {
    vi.mocked(apiClient.get).mockRejectedValue(new Error('offline'))
    renderWithTamagui(<PatientTimelineScreen timeline={[event]} />)

    fireEvent.click(screen.getByText('Pull to refresh'))
    expect(await screen.findByText('Failed to load health timeline.')).toBeTruthy()
    expect(screen.getByText('HbA1c result')).toBeTruthy()
    expect(screen.getByText('Retry')).toBeTruthy()
  })

  it('retries after an initial failure', async () => {
    vi.mocked(apiClient.get)
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce({ data: { events: [event], next_cursor: null } })
    renderWithTamagui(<PatientTimelineScreen />)

    fireEvent.click(await screen.findByText('Retry'))
    expect(await screen.findByText('HbA1c result')).toBeTruthy()
  })

  it('keeps Access History visible outside the SectionList', () => {
    renderWithTamagui(<PatientTimelineScreen timeline={[]} />)

    fireEvent.click(screen.getByText('← Access History'))
    expect(push).toHaveBeenCalledWith('/patient/access-history')
    expect(screen.getByTestId('section-list')).toBeTruthy()
  })
})
