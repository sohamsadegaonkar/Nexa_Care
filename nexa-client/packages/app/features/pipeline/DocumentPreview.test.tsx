import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderWithTamagui } from '../../../../test/test-utils'
import { Provider } from '../../provider'
import { ApiError, NexaApiClient } from '../../utils/apiClient'
import { DocumentPreview } from './DocumentPreview'

const originalCreateObjectURL = Object.getOwnPropertyDescriptor(URL, 'createObjectURL')
const originalRevokeObjectURL = Object.getOwnPropertyDescriptor(URL, 'revokeObjectURL')

function restoreUrlMethods() {
  if (originalCreateObjectURL) {
    Object.defineProperty(URL, 'createObjectURL', originalCreateObjectURL)
  } else {
    Reflect.deleteProperty(URL, 'createObjectURL')
  }
  if (originalRevokeObjectURL) {
    Object.defineProperty(URL, 'revokeObjectURL', originalRevokeObjectURL)
  } else {
    Reflect.deleteProperty(URL, 'revokeObjectURL')
  }
}

function setObjectUrlMethods(create: () => string, revoke: (url: string) => void) {
  Object.defineProperties(URL, {
    createObjectURL: { configurable: true, value: create },
    revokeObjectURL: { configurable: true, value: revoke },
  })
}

function renderPreview(consentToken = 'consent-capability') {
  return renderWithTamagui(
    <DocumentPreview
      jobId="job-1"
      consentToken={consentToken}
      totalPages={2}
      fields={[]}
      highlightedFieldId={null}
      currentPage={2}
      onPageChange={vi.fn()}
    />
  )
}

describe('pipeline source document preview', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    restoreUrlMethods()
  })

  it('does not imply a document is present before an explicit authorized load', () => {
    renderPreview()

    expect(
      screen.getByText(/No document page is shown until the original source is loaded/)
    ).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Load original source document' })).toBeTruthy()
    expect(screen.queryByLabelText('Original source document')).toBeNull()
    expect(document.querySelectorAll('svg rect')).toHaveLength(0)
  })

  it('shows the current page field count without implying clinical verification', () => {
    const onPageChange = vi.fn()
    renderWithTamagui(
      <DocumentPreview
        jobId="job-1"
        consentToken="consent-capability"
        totalPages={2}
        fields={[
          { field_id: 'field-1', field_name: 'Allergy', source_page: 1, source_bbox: null, status: 'needs_review', risk_level: 'HIGH_RISK' },
          { field_id: 'field-2', field_name: 'Medicine', source_page: 2, source_bbox: null, status: 'needs_review', risk_level: 'HIGH_RISK' },
          { field_id: 'field-3', field_name: 'Diagnosis', source_page: 2, source_bbox: null, status: 'approved', risk_level: 'LOW_RISK' },
        ]}
        highlightedFieldId={null}
        currentPage={2}
        onPageChange={onPageChange}
      />
    )

    expect(screen.getByText('2 extracted fields on this page')).toBeTruthy()
    expect(screen.getByText(/ALPHA — extracted fields require clinical verification/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Show page 1' }))
    expect(onPageChange).toHaveBeenCalledWith(1)
  })

  it('loads the PDF through the consent-gated API and revokes its object URL', async () => {
    const source = vi
      .spyOn(NexaApiClient, 'getExtractionJobDocument')
      .mockResolvedValue(new Blob(['protected pdf'], { type: 'application/pdf' }))
    const create = vi.fn(() => 'blob:protected-source')
    const revoke = vi.fn()
    setObjectUrlMethods(create, revoke)

    const rendered = renderPreview()
    fireEvent.click(screen.getByRole('button', { name: 'Load original source document' }))

    const pdf = await screen.findByLabelText('Original source document')
    expect(source).toHaveBeenCalledWith('job-1', 'consent-capability')
    expect(pdf.getAttribute('data')).toBe('blob:protected-source#page=2')
    expect(create).toHaveBeenCalledOnce()

    rendered.unmount()
    expect(revoke).toHaveBeenCalledWith('blob:protected-source')
  })

  it('fails closed without consent and gives a retry after access is denied', async () => {
    const source = vi
      .spyOn(NexaApiClient, 'getExtractionJobDocument')
      .mockRejectedValueOnce(new ApiError('forbidden', 403, 'CONSENT_REQUIRED'))
      .mockResolvedValueOnce(new Blob(['protected image'], { type: 'image/png' }))
    const create = vi.fn(() => 'blob:protected-image')
    setObjectUrlMethods(create, vi.fn())

    const rendered = renderPreview('')
    const disabledLoad = screen.getByRole('button', { name: 'Load original source document' })
    expect(disabledLoad.getAttribute('aria-disabled')).toBe('true')
    expect(source).not.toHaveBeenCalled()

    rendered.rerender(
      <Provider defaultTheme="light">
      <DocumentPreview
        jobId="job-1"
        consentToken="consent-capability"
        totalPages={1}
        fields={[]}
        highlightedFieldId={null}
        currentPage={1}
        onPageChange={vi.fn()}
      />
      </Provider>
    )
    fireEvent.click(screen.getByRole('button', { name: 'Load original source document' }))
    expect(
      await screen.findByText(/current consent does not permit viewing this source/i)
    ).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Retry loading original source document' }))
    expect(await screen.findByAltText('Original source document page')).toBeTruthy()
    expect(source).toHaveBeenCalledTimes(2)
    rendered.unmount()
  })

  it('hides the loaded source when the consent capability changes', async () => {
    const source = vi
      .spyOn(NexaApiClient, 'getExtractionJobDocument')
      .mockResolvedValue(new Blob(['protected pdf'], { type: 'application/pdf' }))
    const revoke = vi.fn()
    setObjectUrlMethods(() => 'blob:protected-source', revoke)
    const rendered = renderPreview()

    fireEvent.click(screen.getByRole('button', { name: 'Load original source document' }))
    await screen.findByLabelText('Original source document')
    rendered.rerender(
      <Provider defaultTheme="light">
      <DocumentPreview
        jobId="job-1"
        consentToken="renewed-consent-capability"
        totalPages={2}
        fields={[]}
        highlightedFieldId={null}
        currentPage={2}
        onPageChange={vi.fn()}
      />
      </Provider>
    )

    expect(screen.queryByLabelText('Original source document')).toBeNull()
    await waitFor(() => expect(revoke).toHaveBeenCalledWith('blob:protected-source'))
    expect(source).toHaveBeenCalledOnce()
  })
})
