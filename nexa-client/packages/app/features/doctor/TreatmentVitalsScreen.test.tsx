import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithTamagui } from '../../../../test/test-utils'
import { ApiError, NexaApiClient } from '../../utils/apiClient'
import { TreatmentVitalsScreen } from './TreatmentVitalsScreen'

const push = vi.fn()
const clearDiscoverySelection = vi.fn()
const setTreatmentSession = vi.fn()
const setTreatmentEncounter = vi.fn()
const clearTreatmentSession = vi.fn()

let providerState: any

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push }),
}))

vi.mock('./ProviderAuthContext', () => ({
  useProviderAuth: () => providerState,
}))

function readyGrant() {
  return {
    requestId: 'request-synthetic',
    treatmentToken: 'synthetic-treatment-capability',
    allowedOperations: ['CREATE_ENCOUNTER', 'WRITE_VITALS'],
    expiresAt: '2099-09-19T09:00:00Z',
    patientDisplayIdentifier: 'NC-SYNTHETIC',
    encounterId: 'encounter-server-owned',
  }
}

describe('provider Treatment Session vitals workflow', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    push.mockReset()
    clearDiscoverySelection.mockReset()
    setTreatmentSession.mockReset()
    setTreatmentEncounter.mockReset()
    clearTreatmentSession.mockReset()
    providerState = {
      isAuthenticated: true,
      displayName: 'Synthetic Provider',
      hospitalName: 'Synthetic Hospital',
      discoverySelection: null,
      clearDiscoverySelection,
      treatmentSession: readyGrant(),
      setTreatmentSession,
      setTreatmentEncounter,
      clearTreatmentSession,
    }
  })

  it('requests exactly CREATE_ENCOUNTER plus WRITE_VITALS from a one-time discovery handle', async () => {
    providerState.treatmentSession = null
    providerState.discoverySelection = {
      discoveryHandle: 'd'.repeat(64),
      expiresAt: '2099-09-19T09:00:00Z',
      displayIdentifier: 'NC-SYNTHETIC',
      source: 'public_id',
    }
    const create = vi.spyOn(NexaApiClient, 'createTreatmentSessionV1Request').mockResolvedValue({
      protocol_version: 'nexa-treatment-session-v1',
      request_id: 'request-synthetic',
      status: 'pending',
      expires_in_seconds: 120,
      challenge_nonce: 'nonce-synthetic',
      treatment_context_hash: 'c'.repeat(64),
    })
    vi.spyOn(NexaApiClient, 'claimTreatmentSessionV1').mockRejectedValue(
      new ApiError('Not approved', 409, 'TREATMENT_NOT_APPROVED', false)
    )

    renderWithTamagui(<TreatmentVitalsScreen />)

    await waitFor(() =>
      expect(create).toHaveBeenCalledWith({
        protocol_version: 'nexa-treatment-session-v1',
        discovery_handle: 'd'.repeat(64),
        purpose: 'record_vitals',
        allowed_operations: ['CREATE_ENCOUNTER', 'WRITE_VITALS'],
        access_duration_seconds: 900,
      })
    )
    expect(clearDiscoverySelection).toHaveBeenCalledOnce()
    expect(JSON.stringify(create.mock.calls[0]?.[0])).not.toContain('WRITE_PRESCRIPTION')
  })

  it('establishes the canonical Encounter before enabling any clinical write', async () => {
    providerState.treatmentSession = { ...readyGrant(), encounterId: null }
    const encounter = vi.spyOn(NexaApiClient, 'createTreatmentSessionEncounter').mockResolvedValue({
      encounter_id: 'encounter-server-owned',
      clinical_session_id: 'session-server-owned',
    })
    const write = vi.spyOn(NexaApiClient, 'writeTreatmentSessionVital')

    renderWithTamagui(<TreatmentVitalsScreen />)

    await waitFor(() =>
      expect(encounter).toHaveBeenCalledWith('synthetic-treatment-capability')
    )
    expect(setTreatmentEncounter).toHaveBeenCalledWith('encounter-server-owned')
    expect(write).not.toHaveBeenCalled()
  })

  it('retries an uncertain unchanged observation with the same idempotency key and never calls legacy appendVitals', async () => {
    const write = vi
      .spyOn(NexaApiClient, 'writeTreatmentSessionVital')
      .mockRejectedValueOnce(new ApiError('Network lost', 0, 'NETWORK_ERROR', true))
      .mockResolvedValueOnce({
        status: 'committed',
        record_id: 'record-synthetic',
        encounter_id: 'encounter-server-owned',
        vital_type: 'HR',
        recorded_at: '2026-09-19T09:00:00Z',
        idempotent_replay: true,
      })
    const legacy = vi.spyOn(NexaApiClient, 'appendVitals')

    renderWithTamagui(<TreatmentVitalsScreen />)
    fireEvent.click(screen.getByRole('button', { name: 'Heart rate' }))
    fireEvent.change(screen.getByLabelText('Heart rate (bpm)'), {
      target: { value: '72' },
    })
    fireEvent.change(screen.getByLabelText('Observed at (ISO 8601 with timezone)'), {
      target: { value: '2026-09-19T09:00:00Z' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Commit Observation' }))

    expect(await screen.findByText(/outcome is uncertain/i)).toBeTruthy()
    fireEvent.click(
      screen.getByRole('button', { name: 'Retry Same Observation' })
    )

    await waitFor(() => expect(write).toHaveBeenCalledTimes(2))
    expect(write.mock.calls[0]?.[1]).toBe(write.mock.calls[1]?.[1])
    expect(write.mock.calls[0]?.[2]).toEqual({
      kind: 'heart_rate',
      beats_per_minute: 72,
      recorded_at: '2026-09-19T09:00:00Z',
    })
    expect(legacy).not.toHaveBeenCalled()
    expect(await screen.findByText('Observation committed successfully.')).toBeTruthy()
  })

  it('blocks rapid double submission while one mutation is in flight', async () => {
    let resolveWrite!: (value: any) => void
    const write = vi.spyOn(NexaApiClient, 'writeTreatmentSessionVital').mockReturnValue(
      new Promise((resolve) => {
        resolveWrite = resolve
      })
    )
    renderWithTamagui(<TreatmentVitalsScreen />)
    fireEvent.click(screen.getByRole('button', { name: 'Heart rate' }))
    fireEvent.change(screen.getByLabelText('Heart rate (bpm)'), {
      target: { value: '72' },
    })
    fireEvent.change(screen.getByLabelText('Observed at (ISO 8601 with timezone)'), {
      target: { value: '2026-09-19T09:00:00Z' },
    })
    const submit = screen.getByRole('button', { name: 'Commit Observation' })
    fireEvent.click(submit)
    fireEvent.click(submit)
    expect(write).toHaveBeenCalledTimes(1)

    resolveWrite({
      status: 'committed',
      record_id: 'record-synthetic',
      encounter_id: 'encounter-server-owned',
      vital_type: 'HR',
      recorded_at: '2026-09-19T09:00:00Z',
      idempotent_replay: false,
    })
    await screen.findByText('Observation committed successfully.')
  })

  it('does not treat a wrong-operation denial as a network retry', async () => {
    vi.spyOn(NexaApiClient, 'writeTreatmentSessionVital').mockRejectedValue(
      new ApiError('Wrong operation', 403, 'TREATMENT_OPERATION_NOT_AUTHORIZED', false)
    )
    renderWithTamagui(<TreatmentVitalsScreen />)
    fireEvent.click(screen.getByRole('button', { name: 'Heart rate' }))
    fireEvent.change(screen.getByLabelText('Heart rate (bpm)'), {
      target: { value: '72' },
    })
    fireEvent.change(screen.getByLabelText('Observed at (ISO 8601 with timezone)'), {
      target: { value: '2026-09-19T09:00:00Z' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Commit Observation' }))

    expect(await screen.findByText(/does not authorize WRITE_VITALS/i)).toBeTruthy()
    expect(clearTreatmentSession).toHaveBeenCalled()
    expect(screen.queryByText('Retry Same Observation')).toBeNull()
  })

  it('expires stale treatment authority instead of falling back to Signed Consent V3', async () => {
    providerState.treatmentSession = {
      ...readyGrant(),
      expiresAt: '2000-01-01T00:00:00Z',
    }
    renderWithTamagui(<TreatmentVitalsScreen />)
    expect(await screen.findByText(/Treatment Session expired/i)).toBeTruthy()
    expect(clearTreatmentSession).toHaveBeenCalled()
    expect(vi.spyOn(NexaApiClient, 'appendVitals')).not.toHaveBeenCalled()
  })
})
