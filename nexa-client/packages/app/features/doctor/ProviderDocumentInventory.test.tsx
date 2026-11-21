import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithTamagui } from '../../../../test/test-utils'
import { ApiError, NexaApiClient } from '../../utils/apiClient'
import { ProviderDocumentInventory } from './ProviderDocumentInventory'

const args = {
  patientId: 'patient-1',
  consentToken: 'memory-only-read-token',
  hospitalId: 'hospital-1',
}

const imported = {
  document_id: 'doc-1',
  document_type: 'LAB_REPORT',
  uploaded_at: '2026-09-10T12:00:00Z',
  source_origin: 'patient_imported_external' as const,
  verification_status: 'patient_reviewed_not_clinician_verified' as const,
  original_access: 'separate_authorization_required' as const,
}

describe('ProviderDocumentInventory consent-gated metadata UX', () => {
  beforeEach(() => vi.restoreAllMocks())

  it('renders actual backend rows and explicitly refuses to infer clinical verification', async () => {
    const get = vi.spyOn(NexaApiClient, 'getProviderPatientDocuments').mockResolvedValue({
      items: [imported], next_cursor: null,
    })
    renderWithTamagui(
      <ProviderDocumentInventory {...args} onAuthorizationFailure={vi.fn()} />
    )
    expect(await screen.findByText('LAB REPORT')).toBeTruthy()
    expect(screen.getByText('Patient reviewed · Not clinician verified')).toBeTruthy()
    expect(screen.getByText('Source: Patient-imported external record')).toBeTruthy()
    expect(screen.getByText('Original source: separate authorization required')).toBeTruthy()
    expect(screen.queryByText('Clinician verified')).toBeNull()
    expect(get).toHaveBeenCalledWith(
      'patient-1', 'memory-only-read-token', 'hospital-1', null
    )
  })

  it('lists older source references with server-issued cursor, not arbitrary client paging', async () => {
    const get = vi.spyOn(NexaApiClient, 'getProviderPatientDocuments')
    get.mockResolvedValueOnce({ items: [imported], next_cursor: 'opaque-cursor' })
      .mockResolvedValueOnce({
        items: [{
          ...imported, document_id: 'doc-2', document_type: 'PRESCRIPTION',
          source_origin: 'not_established', verification_status: 'not_established',
        }], next_cursor: null,
      })
    renderWithTamagui(
      <ProviderDocumentInventory {...args} onAuthorizationFailure={vi.fn()} />
    )
    fireEvent.click(await screen.findByText('Load older documents'))
    expect(await screen.findByText('PRESCRIPTION')).toBeTruthy()
    expect(screen.getByText('Clinical verification: Not established')).toBeTruthy()
    expect(get).toHaveBeenCalledWith(
      'patient-1', 'memory-only-read-token', 'hospital-1', 'opaque-cursor'
    )
    expect(screen.getByText('LAB REPORT')).toBeTruthy()
  })

  it('does not confuse request failure with no available document records', async () => {
    const get = vi.spyOn(NexaApiClient, 'getProviderPatientDocuments')
    get.mockRejectedValueOnce(new Error('network offline')).mockResolvedValueOnce({
      items: [], next_cursor: null,
    })
    renderWithTamagui(
      <ProviderDocumentInventory {...args} onAuthorizationFailure={vi.fn()} />
    )
    expect(await screen.findByText(/Document references could not be loaded/)).toBeTruthy()
    expect(screen.queryByText(/No document references recorded/)).toBeNull()
    fireEvent.click(screen.getByText('Retry document list'))
    expect(await screen.findByText(/No document references recorded/)).toBeTruthy()
  })

  it('clears document rows and calls parent authorization guard on terminal access failure', async () => {
    const onAuthorizationFailure = vi.fn()
    vi.spyOn(NexaApiClient, 'getProviderPatientDocuments').mockRejectedValue(
      new ApiError('Consent revoked', 403, 'CONSENT_REVOKED', false)
    )
    renderWithTamagui(
      <ProviderDocumentInventory {...args} onAuthorizationFailure={onAuthorizationFailure} />
    )
    await waitFor(() => expect(onAuthorizationFailure).toHaveBeenCalledOnce())
    expect(screen.queryByText('LAB REPORT')).toBeNull()
    expect(screen.queryByText(/No document references recorded/)).toBeNull()
  })
})
