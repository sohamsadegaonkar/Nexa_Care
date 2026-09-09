import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  storage: new Map<string, string>(),
  tokenProvider: null as null | (() => string | null),
  apiPost: vi.fn(),
}))

vi.mock('expo-secure-store', () => ({
  WHEN_UNLOCKED_THIS_DEVICE_ONLY: 'device-only',
  getItemAsync: vi.fn(async (key: string) => mocks.storage.get(key) ?? null),
  setItemAsync: vi.fn(async (key: string, value: string) => {
    mocks.storage.set(key, value)
  }),
  deleteItemAsync: vi.fn(async (key: string) => {
    mocks.storage.delete(key)
  }),
}))

vi.mock('../utils/apiClient', () => ({
  apiClient: {
    post: mocks.apiPost,
  },
  setAuthTokenProvider: vi.fn((provider: () => string | null) => {
    mocks.tokenProvider = provider
  }),
}))

function patientJwt(patientId: string, expirySeconds: number, jti = 'session-1'): string {
  const payload = Buffer.from(
    JSON.stringify({
      sub: patientId,
      patient_id: patientId,
      exp: expirySeconds,
      jti,
    })
  ).toString('base64url')
  return `header.${payload}.signature`
}

async function loadSession() {
  vi.resetModules()
  return import('./patientAuthSession')
}

describe('patient authentication lifecycle', () => {
  beforeEach(() => {
    mocks.storage.clear()
    mocks.tokenProvider = null
    mocks.apiPost.mockReset()
    mocks.apiPost.mockResolvedValue({ data: undefined })
  })

  it('starts hydrating and becomes unauthenticated without persisted credentials', async () => {
    const session = await loadSession()
    expect(session.getPatientAuthSnapshot()).toMatchObject({ status: 'hydrating', hydrated: false })
    await session.hydratePatientAuthSession()
    expect(session.getPatientAuthSnapshot()).toMatchObject({
      status: 'unauthenticated',
      hydrated: true,
    })
  })

  it('restores a valid session and supplies its current token at request time', async () => {
    const token = patientJwt('patient-a', Math.floor(Date.now() / 1000) + 300)
    const session = await loadSession()
    mocks.storage.set(session.PATIENT_ACCESS_TOKEN_STORAGE_KEY, token)
    session.configurePatientAuthTokenProvider()

    await session.hydratePatientAuthSession()

    expect(session.getPatientAuthSnapshot()).toMatchObject({
      status: 'authenticated',
      hydrated: true,
    })
    expect(mocks.tokenProvider?.()).toBe(token)
  })

  it('deletes an expired restored session', async () => {
    const session = await loadSession()
    mocks.storage.set(session.PATIENT_ACCESS_TOKEN_STORAGE_KEY, patientJwt('patient-a', 1))
    await session.hydratePatientAuthSession()

    expect(session.getPatientAuthSnapshot().status).toBe('expired')
    expect(mocks.storage.has(session.PATIENT_ACCESS_TOKEN_STORAGE_KEY)).toBe(false)
  })

  it('publishes authentication only after OTP credentials are securely persisted', async () => {
    const session = await loadSession()
    const listener = vi.fn()
    session.subscribeToPatientAuth(listener)
    const token = patientJwt('patient-a', Math.floor(Date.now() / 1000) + 300, 'otp-session')

    await session.storePatientAuthSession(token, 'enrollment-token')

    expect(session.getPatientAuthSnapshot().status).toBe('authenticated')
    expect(listener).toHaveBeenCalledOnce()
    expect(mocks.storage.get(session.PATIENT_ACCESS_TOKEN_STORAGE_KEY)).toBe(token)
    expect(mocks.tokenProvider?.()).toBe(token)
  })

  it('stores account authentication without retaining a stale bootstrap grant', async () => {
    const session = await loadSession()
    const token = patientJwt('patient-a', Math.floor(Date.now() / 1000) + 300)
    mocks.storage.set(session.DEVICE_ENROLLMENT_TOKEN_STORAGE_KEY, 'stale-bootstrap-token')

    await session.storePatientAuthSession(token, null)

    expect(mocks.storage.get(session.PATIENT_ACCESS_TOKEN_STORAGE_KEY)).toBe(token)
    expect(mocks.storage.has(session.DEVICE_ENROLLMENT_TOKEN_STORAGE_KEY)).toBe(false)
    expect(session.getPatientAuthSnapshot().status).toBe('authenticated')
  })

  it('revokes the server session before clearing local credentials on logout', async () => {
    const session = await loadSession()
    const token = patientJwt('patient-a', Math.floor(Date.now() / 1000) + 300)
    await session.storePatientAuthSession(token, 'enrollment-token')
    mocks.apiPost.mockImplementationOnce(async () => {
      expect(mocks.tokenProvider?.()).toBe(token)
      return { data: undefined }
    })

    await session.clearPatientAuthSession('logout')

    expect(mocks.apiPost).toHaveBeenCalledOnce()
    expect(mocks.apiPost).toHaveBeenCalledWith('/api/v2/auth/patient/logout')
    expect(session.getPatientAuthSnapshot().status).toBe('unauthenticated')
    expect(mocks.tokenProvider?.()).toBeNull()
    expect(mocks.storage.size).toBe(0)
  })

  it('does not claim logout success when server revocation is unavailable', async () => {
    const session = await loadSession()
    const token = patientJwt('patient-a', Math.floor(Date.now() / 1000) + 300)
    await session.storePatientAuthSession(token, 'enrollment-token')
    mocks.apiPost.mockRejectedValueOnce(new Error('network unavailable'))

    await expect(session.clearPatientAuthSession('logout')).rejects.toThrow('network unavailable')

    expect(session.getPatientAuthSnapshot().status).toBe('authenticated')
    expect(mocks.tokenProvider?.()).toBe(token)
    expect(mocks.storage.get(session.PATIENT_ACCESS_TOKEN_STORAGE_KEY)).toBe(token)
  })

  it('clears locally on expiry without calling the logout endpoint', async () => {
    const session = await loadSession()
    const token = patientJwt('patient-a', Math.floor(Date.now() / 1000) + 300)
    await session.storePatientAuthSession(token, 'enrollment-token')

    await session.clearPatientAuthSession('expired')

    expect(mocks.apiPost).not.toHaveBeenCalled()
    expect(session.getPatientAuthSnapshot().status).toBe('expired')
    expect(mocks.tokenProvider?.()).toBeNull()
    expect(mocks.storage.size).toBe(0)
  })
})
