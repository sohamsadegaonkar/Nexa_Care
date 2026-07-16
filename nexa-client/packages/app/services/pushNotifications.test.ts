import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  post: vi.fn(),
  pushToken: 'ExpoPushToken[device-a]',
  permission: 'granted',
  requestedPermission: 'granted',
  authToken: 'patient-jwt' as string | null,
  auth: {
    status: 'authenticated',
    hydrated: true,
    sessionKey: 'patient-a:session-1',
  } as { status: string; hydrated: boolean; sessionKey: string | null },
  authListeners: new Set<() => void>(),
  clearSession: vi.fn(),
  receivedListener: undefined as ((notification: any) => void) | undefined,
  responseListener: undefined as ((response: any) => void) | undefined,
  lastResponse: null as any,
  removeReceived: vi.fn(),
  removeResponse: vi.fn(),
  clearLastResponse: vi.fn(),
}))

vi.mock('expo-constants', () => ({
  default: {
    easConfig: null,
    expoConfig: { extra: { eas: { projectId: 'project-123' } } },
  },
}))

vi.mock('react-native', () => ({ Platform: { OS: 'android' } }))

vi.mock('expo-notifications', () => ({
  AndroidImportance: { MAX: 5 },
  setNotificationChannelAsync: vi.fn(async () => undefined),
  getPermissionsAsync: vi.fn(async () => ({ status: mocks.permission })),
  requestPermissionsAsync: vi.fn(async () => ({ status: mocks.requestedPermission })),
  getExpoPushTokenAsync: vi.fn(async () => ({ data: mocks.pushToken })),
  setNotificationHandler: vi.fn(),
  addNotificationReceivedListener: vi.fn((listener) => {
    mocks.receivedListener = listener
    return { remove: mocks.removeReceived }
  }),
  addNotificationResponseReceivedListener: vi.fn((listener) => {
    mocks.responseListener = listener
    return { remove: mocks.removeResponse }
  }),
  getLastNotificationResponseAsync: vi.fn(async () => mocks.lastResponse),
  clearLastNotificationResponseAsync: mocks.clearLastResponse,
}))

vi.mock('../utils/apiClient', async (importOriginal) => {
  const original = await importOriginal<typeof import('../utils/apiClient')>()
  return { ...original, apiClient: { post: mocks.post } }
})

vi.mock('./patientAuthSession', () => ({
  getPatientAuthSnapshot: () => mocks.auth,
  getCurrentPatientAccessToken: () => mocks.authToken,
  subscribeToPatientAuth: (listener: () => void) => {
    mocks.authListeners.add(listener)
    return () => mocks.authListeners.delete(listener)
  },
  clearPatientAuthSession: mocks.clearSession.mockImplementation(async () => {
    mocks.authToken = null
    mocks.auth = { status: 'expired', hydrated: true, sessionKey: null }
    for (const listener of mocks.authListeners) listener()
  }),
}))

import * as Notifications from 'expo-notifications'
import { ApiError } from '../utils/apiClient'
import {
  installConsentNotificationListeners,
  getRegisteredPushTokenForCurrentSession,
  registerForPushNotifications,
  registerPushToken,
} from './pushNotifications'

function changeAuth(
  status: string,
  hydrated: boolean,
  sessionKey: string | null,
  token: string | null
) {
  mocks.auth = { status, hydrated, sessionKey }
  mocks.authToken = token
  for (const listener of mocks.authListeners) listener()
}

function notification(requestId: string) {
  return { request: { content: { data: { type: 'consent_approval', request_id: requestId } } } }
}

describe('authenticated patient push registration', () => {
  beforeEach(() => {
    changeAuth(
      'authenticated',
      true,
      `patient-a:session-${Date.now()}-${Math.random()}`,
      'patient-jwt'
    )
    mocks.pushToken = 'ExpoPushToken[device-a]'
    mocks.permission = 'granted'
    mocks.requestedPermission = 'granted'
    mocks.post.mockReset().mockResolvedValue({ data: undefined })
    mocks.clearSession.mockClear()
    mocks.lastResponse = null
    vi.mocked(Notifications.getExpoPushTokenAsync).mockClear()
    vi.mocked(Notifications.requestPermissionsAsync).mockClear()
  })

  it.each([
    ['hydrating', false],
    ['unauthenticated', true],
  ])('does not prompt or call the backend while auth is %s', async (status, hydrated) => {
    changeAuth(status, hydrated, null, null)

    await expect(registerForPushNotifications()).resolves.toBeNull()

    expect(Notifications.getExpoPushTokenAsync).not.toHaveBeenCalled()
    expect(mocks.post).not.toHaveBeenCalled()
  })

  it('registers once after hydration with an authenticated session', async () => {
    await expect(registerForPushNotifications()).resolves.toBe('ExpoPushToken[device-a]')
    expect(mocks.post).toHaveBeenCalledOnce()
    expect(mocks.post).toHaveBeenCalledWith(
      '/api/v2/push/register-token',
      { expo_push_token: 'ExpoPushToken[device-a]', platform: 'android' },
      { signal: undefined }
    )
    expect(getRegisteredPushTokenForCurrentSession()).toBe('ExpoPushToken[device-a]')
  })

  it('returns AUTH_REQUIRED locally without a backend request', async () => {
    mocks.authToken = null
    await expect(registerPushToken('ExpoPushToken[device-a]')).rejects.toMatchObject({
      code: 'AUTH_REQUIRED',
      status: 0,
    })
    expect(mocks.post).not.toHaveBeenCalled()
  })

  it('collapses concurrent and repeated registration for one session/token', async () => {
    let resolvePost!: () => void
    mocks.post.mockReturnValue(
      new Promise((resolve) => {
        resolvePost = () => resolve({ data: undefined })
      })
    )
    const first = registerForPushNotifications()
    const second = registerForPushNotifications()
    await vi.waitFor(() => expect(mocks.post).toHaveBeenCalledOnce())
    resolvePost()
    await Promise.all([first, second])

    await registerForPushNotifications()
    expect(mocks.post).toHaveBeenCalledOnce()
  })

  it('registers again when the push token changes', async () => {
    await registerForPushNotifications()
    mocks.pushToken = 'ExpoPushToken[device-b]'
    await registerForPushNotifications()
    expect(mocks.post).toHaveBeenCalledTimes(2)
  })

  it('registers again for a different patient session and after logout/login', async () => {
    await registerForPushNotifications()
    changeAuth('unauthenticated', true, null, null)
    await registerForPushNotifications()
    changeAuth('authenticated', true, 'patient-b:new-session', 'patient-b-jwt')
    await registerForPushNotifications()
    expect(mocks.post).toHaveBeenCalledTimes(2)
  })

  it('expires a rejected session without console.error or retries', async () => {
    const accessToken = mocks.authToken
    const pushToken = mocks.pushToken
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    mocks.post.mockRejectedValue(new ApiError('Invalid or expired session', 401, 'REAUTH_REQUIRED'))

    await expect(registerForPushNotifications()).resolves.toBeNull()

    expect(mocks.clearSession).toHaveBeenCalledWith('expired')
    expect(errorSpy).not.toHaveBeenCalled()
    expect(warnSpy).toHaveBeenCalledWith('PUSH_SESSION_EXPIRED', {
      path: '/api/v2/push/register-token',
      status: 401,
    })
    const diagnostics = JSON.stringify(warnSpy.mock.calls)
    expect(diagnostics).not.toContain(accessToken)
    expect(diagnostics).not.toContain(pushToken)
    expect(mocks.post).toHaveBeenCalledOnce()
  })

  it('stops before backend registration when its layout effect is cancelled', async () => {
    const controller = new AbortController()
    vi.mocked(Notifications.getPermissionsAsync).mockImplementationOnce(async () => {
      controller.abort()
      return { status: 'granted' } as Notifications.NotificationPermissionsStatus
    })

    await expect(registerForPushNotifications({ signal: controller.signal })).resolves.toBeNull()
    expect(mocks.post).not.toHaveBeenCalled()
  })

  it('routes foreground deliveries, taps, and cold starts without post-unmount navigation', async () => {
    const navigate = vi.fn()
    mocks.lastResponse = { notification: notification('cold-start') }
    const unsubscribe = installConsentNotificationListeners(navigate)
    await vi.waitFor(() => expect(navigate).toHaveBeenCalledWith('cold-start'))
    mocks.receivedListener?.(notification('foreground'))
    mocks.responseListener?.({ notification: notification('tap') })
    unsubscribe()
    mocks.receivedListener?.(notification('after-unmount'))

    expect(navigate).toHaveBeenCalledWith('foreground')
    expect(navigate).toHaveBeenCalledWith('tap')
    expect(navigate).not.toHaveBeenCalledWith('after-unmount')
  })

  it('queues a notification tap until patient authentication hydration completes', async () => {
    changeAuth('hydrating', false, null, null)
    const navigate = vi.fn()
    const unsubscribe = installConsentNotificationListeners(navigate)
    mocks.responseListener?.({ notification: notification('wait-for-auth') })
    expect(navigate).not.toHaveBeenCalled()

    changeAuth('authenticated', true, 'patient-a:hydrated-session', 'patient-jwt')
    expect(navigate).toHaveBeenCalledWith('wait-for-auth')
    unsubscribe()
  })
})
