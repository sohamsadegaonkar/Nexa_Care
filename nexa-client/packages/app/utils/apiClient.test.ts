import { afterEach, describe, expect, it, vi } from 'vitest'

async function loadClient(apiUrl = 'https://native.example.test') {
  vi.resetModules()
  vi.stubGlobal('navigator', { product: 'ReactNative' })
  vi.stubEnv('EXPO_PUBLIC_API_URL', apiUrl)
  vi.stubEnv('EXPO_PUBLIC_APP_ENV', 'preview')
  vi.stubEnv('EXPO_PUBLIC_ALLOW_HTTP', 'false')
  return import('./apiClient')
}

describe('shared API transport', () => {
  afterEach(() => {
    vi.unstubAllEnvs()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    vi.resetModules()
  })

  it('uses the Expo URL, normalizes it, and sends the exact OTP contract without auth', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ message: 'sent' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    )
    vi.stubGlobal('fetch', fetchMock)
    const { API_BASE_URL, apiClient } = await loadClient('https://native.example.test/')

    await apiClient.post('/api/v2/auth/otp/send', { phone: '+919876543210' }, { noAuth: true })

    expect(API_BASE_URL).toBe('https://native.example.test')
    expect(fetchMock).toHaveBeenCalledOnce()
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('https://native.example.test/api/v2/auth/otp/send')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toEqual({ phone: '+919876543210' })
    expect(init.headers.Authorization).toBeUndefined()
  })

  it('keeps the Next.js URL separate even when a mobile variable also exists', async () => {
    vi.resetModules()
    vi.stubGlobal('navigator', { product: 'Gecko' })
    vi.stubEnv('EXPO_PUBLIC_API_URL', 'https://mobile.example.test')
    vi.stubEnv('NEXT_PUBLIC_API_URL', 'https://web.example.test/')
    const { API_BASE_URL } = await import('./apiClient')

    expect(API_BASE_URL).toBe('https://web.example.test')
  })

  it('fails before fetch when no API URL is configured', async () => {
    vi.resetModules()
    vi.stubGlobal('navigator', { product: 'ReactNative' })
    vi.stubEnv('EXPO_PUBLIC_API_URL', '')
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const { apiClient } = await import('./apiClient')

    await expect(
      apiClient.post('/api/v2/auth/otp/send', {}, { noAuth: true })
    ).rejects.toMatchObject({ code: 'MISSING_API_BASE_URL', status: 0 })
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it.each([
    [422, { detail: [{ msg: 'Enter a valid Indian mobile number.' }] }, 'VALIDATION_ERROR'],
    [429, { detail: 'Too many OTP requests. Please try again later.' }, 'RATE_LIMITED'],
  ])('preserves HTTP %s and backend detail', async (status, payload, code) => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    const error = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(payload), {
          status,
          headers: { 'Content-Type': 'application/json' },
        })
      )
    )
    const { apiClient } = await loadClient()

    await expect(
      apiClient.post('/api/v2/auth/otp/send', {}, { noAuth: true })
    ).rejects.toMatchObject({
      status,
      code,
      message: expect.stringMatching(/valid Indian|Too many OTP/),
    })
    expect(warn).toHaveBeenCalledWith(
      `API_REQUEST_REJECTED ${JSON.stringify({
        method: 'POST',
        path: '/api/v2/auth/otp/send',
        status,
        code,
        retryable: status === 429,
      })}`
    )
    expect(error).not.toHaveBeenCalled()
  })

  it('logs revoked patient access as a redacted expected rejection', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    const error = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const patientId = '32e1a052-e82d-48c6-982b-d88f4fc57cb5'
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: {
              message: 'Approved access is no longer valid.',
              error_code: 'CONSENT_REVOKED',
            },
          }),
          {
            status: 403,
            headers: { 'Content-Type': 'application/json' },
          }
        )
      )
    )
    const { NexaApiClient, setAuthTokenProvider } = await loadClient()
    setAuthTokenProvider(() => 'provider-session-token')

    await expect(
      NexaApiClient.getPatientSummary(patientId, 'consent-capability', 'hospital-1')
    ).rejects.toMatchObject({
      status: 403,
      code: 'CONSENT_REVOKED',
      isRetryable: false,
    })
    expect(warn).toHaveBeenCalledWith(
      `API_REQUEST_REJECTED ${JSON.stringify({
        method: 'GET',
        path: '/api/v2/patient/:id/summary',
        status: 403,
        code: 'CONSENT_REVOKED',
        retryable: false,
      })}`
    )
    expect(JSON.stringify(warn.mock.calls)).not.toContain(patientId)
    expect(error).not.toHaveBeenCalled()
  })

  it('attaches bearer authentication and the hospital UUID to consent requests', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          request_id: 'request-1',
          challenge_nonce: 'nonce',
          expires_in_seconds: 300,
          status: 'pending',
        }),
        { status: 201, headers: { 'Content-Type': 'application/json' } }
      )
    )
    vi.stubGlobal('fetch', fetchMock)
    const { NexaApiClient, setAuthTokenProvider } = await loadClient()
    setAuthTokenProvider(() => 'provider-session-token')

    await NexaApiClient.requestConsent(
      {
        patient_id: 'patient-1',
        provider_id: 'provider-1',
        purpose: 'treatment',
        scope: 'clinical',
        access_duration_seconds: 900,
      },
      'hospital-1'
    )

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('https://native.example.test/api/v2/consent/request')
    expect(init.headers.Authorization).toBe('Bearer provider-session-token')
    expect(init.headers['X-Hospital-Id']).toBe('hospital-1')
  })

  it('attaches bearer authentication and the hospital UUID to consent status polling', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          request_id: 'request-1',
          status: 'pending',
          responded_at: null,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    )
    vi.stubGlobal('fetch', fetchMock)
    const { NexaApiClient, setAuthTokenProvider } = await loadClient()
    setAuthTokenProvider(() => 'provider-session-token')

    await NexaApiClient.getConsentStatus('request-1', 'hospital-1')

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('https://native.example.test/api/v2/consent/status/request-1')
    expect(init.headers.Authorization).toBe('Bearer provider-session-token')
    expect(init.headers['X-Hospital-Id']).toBe('hospital-1')
  })

  it('revokes approved access through the authenticated patient endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          request_id: 'request/with space',
          status: 'revoked',
          revoked_at: '2026-07-26T00:00:00+00:00',
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    )
    vi.stubGlobal('fetch', fetchMock)
    const { NexaApiClient, setAuthTokenProvider } = await loadClient()
    setAuthTokenProvider(() => 'patient-session')

    await NexaApiClient.revokeApprovedAccess('request/with space')

    expect(fetchMock).toHaveBeenCalledWith(
      'https://native.example.test/api/v2/consent/request/request%2Fwith%20space/revoke',
      expect.objectContaining({
        method: 'DELETE',
        headers: expect.objectContaining({
          Authorization: 'Bearer patient-session',
        }),
      })
    )
  })

  it('rejects missing consent-status hospital context before fetch', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const { NexaApiClient, setAuthTokenProvider } = await loadClient()
    setAuthTokenProvider(() => 'provider-session-token')

    await expect(NexaApiClient.getConsentStatus('request-1', '  ')).rejects.toMatchObject({
      status: 0,
      code: 'PROVIDER_CONTEXT_REQUIRED',
      isRetryable: false,
    })
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('classifies a true transport failure without exposing the request body', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Network request failed')))
    const { apiClient } = await loadClient()

    await expect(
      apiClient.post(
        '/api/v2/auth/otp/send',
        { phone: '+919876543210', otp: '123456' },
        { noAuth: true }
      )
    ).rejects.toMatchObject({ status: 0, code: 'NETWORK_ERROR' })

    expect(errorSpy).not.toHaveBeenCalled()
    expect(warnSpy).toHaveBeenCalledWith(expect.stringContaining('API_REQUEST_RETRYABLE'))
    const diagnostics = JSON.stringify(warnSpy.mock.calls)
    expect(diagnostics).toContain('/api/v2/auth/otp/send')
    expect(diagnostics).not.toContain('+919876543210')
    expect(diagnostics).not.toContain('123456')
  })

  it('classifies a timeout separately', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(
        (_url, init: RequestInit) =>
          new Promise((_resolve, reject) => {
            init.signal?.addEventListener('abort', () =>
              reject(new DOMException('Aborted', 'AbortError'))
            )
          })
      )
    )
    const { apiClient } = await loadClient()

    await expect(
      apiClient.post('/api/v2/auth/otp/send', {}, { noAuth: true, timeoutMs: 5 })
    ).rejects.toMatchObject({ status: 0, code: 'REQUEST_TIMEOUT' })
    expect(errorSpy).not.toHaveBeenCalled()
    expect(warnSpy).toHaveBeenCalledWith(expect.stringContaining('API_REQUEST_RETRYABLE'))
  })

  it('rejects a malformed successful response', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('not-json', { status: 200 })))
    const { apiClient } = await loadClient()

    await expect(apiClient.get('/health', { noAuth: true })).rejects.toMatchObject({
      code: 'MALFORMED_RESPONSE',
    })
    expect(errorSpy).toHaveBeenCalledWith(expect.stringContaining('API_REQUEST_ERROR'))
  })

  it('fails an authenticated request locally when the current token is missing', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const { apiClient, setAuthTokenProvider } = await loadClient()
    setAuthTokenProvider(() => null)

    await expect(apiClient.post('/api/v2/push/register-token', {})).rejects.toMatchObject({
      status: 0,
      code: 'AUTH_REQUIRED',
    })
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('reads the latest token for every request instead of caching one at module load', async () => {
    const fetchMock = vi.fn().mockImplementation(
      async () =>
        new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
    )
    vi.stubGlobal('fetch', fetchMock)
    const { apiClient, setAuthTokenProvider } = await loadClient()
    let token = 'first-session'
    setAuthTokenProvider(() => token)

    await apiClient.get('/api/v2/patient/devices')
    token = 'current-session'
    await apiClient.get('/api/v2/patient/devices')

    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe('Bearer first-session')
    expect(fetchMock.mock.calls[1][1].headers.Authorization).toBe('Bearer current-session')
  })

  it('sends provider password and MFA requests through the typed unauthenticated endpoints', async () => {
    const responses = [
      { detail: 'Multi-factor authentication required.', mfa_token: 'pending-token' },
      {
        access_token: 'provider-token',
        token_type: 'bearer',
        expires_at: '2099-01-01T00:00:00Z',
        provider_uid: 'provider-1',
        hospital_id: 'hospital-1',
      },
    ]
    const fetchMock = vi.fn().mockImplementation(
      async () =>
        new Response(JSON.stringify(responses.shift()), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
    )
    vi.stubGlobal('fetch', fetchMock)
    const { NexaApiClient } = await loadClient()

    await NexaApiClient.providerLogin({
      login_identifier: 'doctor@example.test',
      password: 'runtime-only-password',
    })
    await NexaApiClient.providerMfaVerify({
      mfa_token: 'pending-token',
      totp_code: '123456',
    })

    expect(fetchMock.mock.calls[0][0]).toBe('https://native.example.test/api/v2/auth/login')
    expect(fetchMock.mock.calls[1][0]).toBe('https://native.example.test/api/v2/auth/mfa/verify')
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBeUndefined()
    expect(fetchMock.mock.calls[1][1].headers.Authorization).toBeUndefined()
  })
})

describe('adjudication API contract', () => {
  afterEach(() => {
    vi.unstubAllEnvs()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    vi.resetModules()
  })

  it('uses case APIs and sends the review session only in the source header', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify([
            {
              case_id: 'case-1',
              status: 'PENDING',
            },
          ]),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        )
      )
      .mockResolvedValueOnce(
        new Response('document', {
          status: 200,
          headers: { 'Content-Type': 'application/pdf' },
        })
      )
    vi.stubGlobal('fetch', fetchMock)
    const { NexaApiClient, setAuthTokenProvider } = await loadClient()
    setAuthTokenProvider(() => 'provider-token')

    await NexaApiClient.listAdjudicationCases()
    await NexaApiClient.getAdjudicationSource('case-1', 'review-session-secret')

    expect(fetchMock.mock.calls[0][0]).toBe(
      'https://native.example.test/api/v2/pipeline/adjudication-cases'
    )
    const [sourceUrl, sourceInit] = fetchMock.mock.calls[1]
    expect(sourceUrl).toBe(
      'https://native.example.test/api/v2/pipeline/adjudication-cases/case-1/source'
    )
    expect(sourceUrl).not.toContain('review-session-secret')
    expect(sourceInit.headers['X-Review-Session-ID']).toBe('review-session-secret')
  })

  it('commits through the accepted-submission endpoint, never the legacy job endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          submission_id: 'submission-1',
          case_id: 'case-1',
          status: 'committed',
          committed_at: '2026-07-29T12:00:00Z',
          provenance: 'human_adjudicated',
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    )
    vi.stubGlobal('fetch', fetchMock)
    const { NexaApiClient, setAuthTokenProvider } = await loadClient()
    setAuthTokenProvider(() => 'provider-token')

    await NexaApiClient.commitAdjudicationSubmission('submission-1', 'review-session-secret')

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toContain('/api/v2/pipeline/adjudication-submissions/submission-1/commit')
    expect(url).not.toContain('/jobs/')
    expect(init.headers['X-Review-Session-ID']).toBe('review-session-secret')
  })

  it('rotates a pending session through a body-only recovery endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          case_id: 'case-1',
          status: 'PENDING',
          version: 2,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    )
    vi.stubGlobal('fetch', fetchMock)
    const { NexaApiClient, setAuthTokenProvider } = await loadClient()
    setAuthTokenProvider(() => 'provider-token')

    await NexaApiClient.recoverAdjudicationSession('case-1', 'review-session-new')

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe(
      'https://native.example.test/api/v2/pipeline/adjudication-cases/case-1/recover-session'
    )
    expect(url).not.toContain('review-session-new')
    expect(JSON.parse(init.body)).toEqual({ review_session_id: 'review-session-new' })
  })
})
