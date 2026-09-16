import { beforeEach, describe, expect, it, vi } from 'vitest'

const { post } = vi.hoisted(() => ({ post: vi.fn() }))

vi.mock('../utils/apiClient', () => ({
  apiClient: { post },
}))

import { discoverPatientExact } from './patientDiscovery'

describe('patient discovery transport', () => {
  beforeEach(() => {
    post.mockReset()
    post.mockResolvedValue({
      data: {
        discovery_handle: 'opaque-handle',
        expires_at: '2026-09-16T10:00:00Z',
      },
    })
  })

  it.each([
    ['NEXA_PUBLIC_ID', 'NC-ABABABABABABABABABABABAB'],
    ['PHONE', '+918000000001'],
    ['QR_PUBLIC_ID', 'nexa://patient-discovery/v1/NC-ABABABABABABABABABABABAB'],
  ] as const)('sends %s only in the POST body', async (identifier_type, value) => {
    const result = await discoverPatientExact(
      { identifier_type, value },
      'hospital-1'
    )

    expect(result.discovery_handle).toBe('opaque-handle')
    expect(post).toHaveBeenCalledWith(
      '/api/v2/patient-discovery',
      { identifier_type, value },
      { headers: { 'X-Hospital-Id': 'hospital-1' } }
    )
    expect(post.mock.calls[0]?.[0]).not.toContain(value)
  })
})
