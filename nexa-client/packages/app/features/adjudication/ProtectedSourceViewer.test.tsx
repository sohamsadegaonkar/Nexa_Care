import { act, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderWithTamagui } from '../../../../test/test-utils'
import { NexaApiClient } from '../../utils/apiClient'
import { ProtectedSourceViewer } from './ProtectedSourceViewer'

describe('protected adjudication source viewer', () => {
  afterEach(() => vi.restoreAllMocks())

  it('sends the authoritative session and revokes the temporary object URL on unmount', async () => {
    const source = vi
      .spyOn(NexaApiClient, 'getAdjudicationSource')
      .mockResolvedValue(new Blob(['source'], { type: 'image/png' }))
    const create = vi.fn(() => 'blob:protected-source')
    const revoke = vi.fn()
    Object.defineProperties(URL, {
      createObjectURL: { configurable: true, value: create },
      revokeObjectURL: { configurable: true, value: revoke },
    })
    const rendered = renderWithTamagui(
      <ProtectedSourceViewer
        caseId="case-1"
        reviewSessionId="review-session-1"
        onTerminalAccessFailure={vi.fn()}
      />
    )
    await act(async () => Promise.resolve())
    expect(source).toHaveBeenCalledWith('case-1', 'review-session-1')
    expect(create).toHaveBeenCalledOnce()
    expect(await screen.findByAltText('Protected source document')).toBeTruthy()
    rendered.unmount()
    expect(revoke).toHaveBeenCalledWith('blob:protected-source')
  })
})
