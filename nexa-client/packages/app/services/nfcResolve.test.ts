import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NexaApiClient } from '../utils/apiClient'
import { resolveNfcCard } from './nfcResolve'

describe('resolveNfcCard', () => {
  beforeEach(() => vi.restoreAllMocks())

  it('posts the UID and returns only the opaque discovery capability', async () => {
    const resolve = vi.spyOn(NexaApiClient, 'resolveNfcCard').mockResolvedValue({
      discovery_handle: 'h'.repeat(32),
      expires_at: '2026-08-27T12:00:00Z',
    })

    await expect(resolveNfcCard('  CARD-123  ')).resolves.toEqual({
      discovery_handle: 'h'.repeat(32),
      expires_at: '2026-08-27T12:00:00Z',
    })
    expect(resolve).toHaveBeenCalledWith({ card_uid: 'CARD-123' })
  })

  it('rejects malformed responses without exposing identity fields', async () => {
    vi.spyOn(NexaApiClient, 'resolveNfcCard').mockResolvedValue({
      patient_id: 'internal-id',
    })

    await expect(resolveNfcCard('CARD-123')).rejects.toMatchObject({
      code: 'NFC_SCHEMA_VALIDATION_FAILED',
      retryable: false,
    })
  })

  it('maps unavailable resolution to a retryable generic error', async () => {
    vi.spyOn(NexaApiClient, 'resolveNfcCard').mockRejectedValue({ status: 503 })

    await expect(resolveNfcCard('CARD-123')).rejects.toMatchObject({
      code: 'NFC_RESOLVE_UNAVAILABLE',
      retryable: true,
      message: 'NFC resolve service is temporarily unavailable.',
    })
  })
})
