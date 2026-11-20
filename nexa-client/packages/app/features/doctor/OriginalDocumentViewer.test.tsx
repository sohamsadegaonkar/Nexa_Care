import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { renderWithTamagui } from '../../../../test/test-utils'
import { NexaApiClient, type OriginalReceipt } from '../../utils/apiClient'
import { OriginalDocumentViewer } from './OriginalDocumentViewer'

const access = { patientId: 'synthetic', hospitalId: 'facility', consentToken: 'test-only' }
const receipt = (status: OriginalReceipt['status']): OriginalReceipt => ({
  request_id: 'request', expires_at: new Date(Date.now() + 60000).toISOString(), status,
})
// jsdom does not implement the browser's object URL APIs. Install explicit
// test doubles and restore the original global surface after each test.
const createDescriptor = Object.getOwnPropertyDescriptor(URL, 'createObjectURL')
const revokeDescriptor = Object.getOwnPropertyDescriptor(URL, 'revokeObjectURL')
beforeEach(() => {
  Object.defineProperty(URL, 'createObjectURL', {
    configurable: true, writable: true, value: vi.fn(() => 'blob:temporary'),
  })
  Object.defineProperty(URL, 'revokeObjectURL', {
    configurable: true, writable: true, value: vi.fn(),
  })
})
afterEach(() => {
  vi.restoreAllMocks()
  if (createDescriptor) Object.defineProperty(URL, 'createObjectURL', createDescriptor)
  else Reflect.deleteProperty(URL, 'createObjectURL')
  if (revokeDescriptor) Object.defineProperty(URL, 'revokeObjectURL', revokeDescriptor)
  else Reflect.deleteProperty(URL, 'revokeObjectURL')
})

it('closes the visible image and releases its temporary URL after revocation', async () => {
  vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:temporary')
  const release = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
  vi.spyOn(NexaApiClient, 'requestOriginal').mockResolvedValue(receipt('approved'))
  vi.spyOn(NexaApiClient, 'openOriginal').mockResolvedValue(new Blob(['synthetic'], { type: 'image/png' }))
  vi.spyOn(NexaApiClient, 'originalStatus').mockResolvedValueOnce(receipt('opened')).mockResolvedValue(receipt('revoked'))
  renderWithTamagui(<OriginalDocumentViewer documentId="one" access={access} />)
  fireEvent.click(screen.getByText('Request original document'))
  fireEvent.click(await screen.findByText('Open approved original'))
  expect(await screen.findByAltText('Original medical report')).toBeTruthy()
  await waitFor(() => expect(release).toHaveBeenCalledWith('blob:temporary'), { timeout: 4000 })
  expect(screen.queryByAltText('Original medical report')).toBeNull()
})

it('discards a late open when its patient scope has unmounted', async () => {
  const create = vi.spyOn(URL, 'createObjectURL')
  vi.spyOn(NexaApiClient, 'requestOriginal').mockResolvedValue(receipt('approved'))
  let deliver!: (blob: Blob) => void
  vi.spyOn(NexaApiClient, 'openOriginal').mockImplementation(() => new Promise(resolve => { deliver = resolve }))
  vi.spyOn(NexaApiClient, 'originalStatus').mockResolvedValue(receipt('opened'))
  const { unmount } = renderWithTamagui(<OriginalDocumentViewer documentId="two" access={access} />)
  fireEvent.click(screen.getByText('Request original document'))
  fireEvent.click(await screen.findByText('Open approved original'))
  unmount()
  deliver(new Blob(['synthetic'], { type: 'image/png' }))
  await new Promise(resolve => setTimeout(resolve, 0))
  expect(create).not.toHaveBeenCalled()
})
