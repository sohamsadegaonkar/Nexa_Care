import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { GET } from '../app/readyz/route'

const api = 'https://nexa-stg-api.internal.sample-123.centralindia.azurecontainerapps.io'
const web = 'https://nexa-stg-web.sample-123.centralindia.azurecontainerapps.io'
const backendReady = {
  status: 'ok',
  checks: {
    postgres: 'ok',
    redis: 'ok',
    workers: 'ok',
    audit_outbox: 'ok',
    patient_source_scanner: 'ok',
  },
}

describe('Azure staging dependency readiness', () => {
  beforeEach(() => {
    vi.stubEnv('NEXA_AZURE_STAGING_RUNTIME', '1')
    vi.stubEnv('NEXA_AZURE_STAGING_EXPECTED_API_ORIGIN', api)
    vi.stubEnv('NEXA_AZURE_STAGING_EXPECTED_WEB_ORIGIN', web)
    vi.stubEnv('API_PROXY_TARGET', api)
    vi.stubEnv('NEXT_PUBLIC_API_URL', web)
  })

  afterEach(() => {
    vi.unstubAllEnvs()
    vi.unstubAllGlobals()
  })

  it('requires all real backend readiness checks', async () => {
    const fetch = vi.fn().mockResolvedValue(Response.json(backendReady))
    vi.stubGlobal('fetch', fetch)
    const response = await GET()
    expect(response.status).toBe(200)
    expect(await response.json()).toEqual({ status: 'ready', scope: 'dependencies' })
    expect(fetch).toHaveBeenCalledWith(
      `${api}/health`,
      expect.objectContaining({ cache: 'no-store', redirect: 'error' })
    )
  })

  it.each([
    { status: 'ok' },
    { ...backendReady, checks: { ...backendReady.checks, patient_source_scanner: 'unavailable' } },
  ])('blocks an incomplete readiness response', async (payload) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(payload)))
    expect((await GET()).status).toBe(503)
  })

  it('refuses a build configured for a different environment before fetching', async () => {
    vi.stubEnv('API_PROXY_TARGET', 'https://other.example.test')
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    expect((await GET()).status).toBe(503)
    expect(fetch).not.toHaveBeenCalled()
  })

  it('keeps the Render preview blocked', async () => {
    vi.stubEnv('NEXA_AZURE_STAGING_RUNTIME', '')
    vi.stubEnv('NEXA_STAGING_WEB_PREVIEW', '1')
    expect((await GET()).status).toBe(503)
  })
})
