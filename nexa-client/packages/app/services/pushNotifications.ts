/** Patient push registration and consent-notification routing. */

import Constants from 'expo-constants'
import * as Notifications from 'expo-notifications'
import { Platform } from 'react-native'
import { ApiError, apiClient } from '../utils/apiClient'
import {
  clearPatientAuthSession,
  getCurrentPatientAccessToken,
  getPatientAuthSnapshot,
  subscribeToPatientAuth,
} from './patientAuthSession'

export interface PushTokenRegistration {
  expo_push_token: string
  platform: 'ios' | 'android'
}

export interface PushRegistrationOptions {
  signal?: AbortSignal
}

export type ConsentNotificationNavigator = (requestId: string) => void

const CONSENT_CHANNEL_ID = 'consent-requests'
let registrationInFlight: { sessionKey: string; promise: Promise<string | null> } | null = null
let registered: { sessionKey: string; expoPushToken: string } | null = null
let notificationHandlerConfigured = false
let observedSessionKey = getPatientAuthSnapshot().sessionKey

subscribeToPatientAuth(() => {
  const nextSessionKey = getPatientAuthSnapshot().sessionKey
  if (nextSessionKey !== observedSessionKey) {
    observedSessionKey = nextSessionKey
    registered = null
  }
})

/** Return the in-memory token only for the currently authenticated session. */
export function getRegisteredPushTokenForCurrentSession(): string | null {
  const sessionKey = getPatientAuthSnapshot().sessionKey
  return registered?.sessionKey === sessionKey ? registered.expoPushToken : null
}

function getProjectId(): string {
  const projectId = Constants.easConfig?.projectId ?? Constants.expoConfig?.extra?.eas?.projectId
  if (typeof projectId !== 'string' || !projectId.trim()) {
    throw new Error('Expo EAS project ID is missing from app configuration.')
  }
  return projectId
}

async function configureAndroidChannel(): Promise<void> {
  if (Platform.OS !== 'android') return
  await Notifications.setNotificationChannelAsync(CONSENT_CHANNEL_ID, {
    name: 'Consent requests',
    description: 'Requests from verified providers that require patient review.',
    importance: Notifications.AndroidImportance.MAX,
    vibrationPattern: [0, 250, 250, 250],
    lightColor: '#0A84FF',
    sound: 'default',
  })
}

export async function registerPushToken(
  expoPushToken: string,
  signal?: AbortSignal
): Promise<void> {
  if (!getCurrentPatientAccessToken()) {
    throw new ApiError(
      'Patient authentication is required for push registration.',
      0,
      'AUTH_REQUIRED'
    )
  }
  const payload: PushTokenRegistration = {
    expo_push_token: expoPushToken,
    platform: Platform.OS === 'ios' ? 'ios' : 'android',
  }
  await apiClient.post(
    '/api/v2/push/register-token',
    payload as unknown as Record<string, unknown>,
    { signal }
  )
}

async function performPushRegistration(
  sessionKey: string,
  options: PushRegistrationOptions
): Promise<string | null> {
  if (Platform.OS === 'web' || options.signal?.aborted) return null

  const auth = getPatientAuthSnapshot()
  if (!auth.hydrated || auth.status !== 'authenticated' || auth.sessionKey !== sessionKey)
    return null
  if (!getCurrentPatientAccessToken()) return null

  await configureAndroidChannel()
  if (options.signal?.aborted) return null
  const currentPermission = await Notifications.getPermissionsAsync()
  const permission =
    currentPermission.status === 'granted'
      ? currentPermission
      : await Notifications.requestPermissionsAsync()

  if (permission.status !== 'granted') {
    console.warn('PUSH_NOTIFICATION_PERMISSION_NOT_GRANTED')
    return null
  }
  if (options.signal?.aborted) return null

  const token = (await Notifications.getExpoPushTokenAsync({ projectId: getProjectId() })).data
  if (options.signal?.aborted) return null

  const latestAuth = getPatientAuthSnapshot()
  if (latestAuth.status !== 'authenticated' || latestAuth.sessionKey !== sessionKey) return null
  if (registered?.sessionKey === sessionKey && registered.expoPushToken === token) return token

  try {
    await registerPushToken(token, options.signal)
  } catch (error) {
    if (error instanceof ApiError && error.code === 'AUTH_REQUIRED') return null
    if (error instanceof ApiError && error.status === 401) {
      registered = null
      await clearPatientAuthSession('expired')
      if (process.env.NODE_ENV !== 'production') {
        console.warn('PUSH_SESSION_EXPIRED', { path: '/api/v2/push/register-token', status: 401 })
      }
      return null
    }
    throw error
  }

  if (!options.signal?.aborted && getPatientAuthSnapshot().sessionKey === sessionKey) {
    registered = { sessionKey, expoPushToken: token }
  }
  return token
}

/** Obtain and upsert a token only for a fully hydrated patient session. */
export function registerForPushNotifications(
  options: PushRegistrationOptions = {}
): Promise<string | null> {
  const auth = getPatientAuthSnapshot()
  if (!auth.hydrated || auth.status !== 'authenticated' || !auth.sessionKey) {
    return Promise.resolve(null)
  }
  if (!getCurrentPatientAccessToken()) return Promise.resolve(null)
  if (registrationInFlight?.sessionKey === auth.sessionKey) return registrationInFlight.promise

  const promise = performPushRegistration(auth.sessionKey, options).finally(() => {
    if (registrationInFlight?.promise === promise) registrationInFlight = null
  })
  registrationInFlight = { sessionKey: auth.sessionKey, promise }
  return promise
}

export function extractRequestIdFromNotification(
  notificationData: Record<string, unknown>
): string | null {
  const requestId =
    notificationData.request_id ??
    (notificationData.data as Record<string, unknown> | undefined)?.request_id ??
    null
  return typeof requestId === 'string' && requestId.trim() ? requestId.trim() : null
}

function requestIdFromNotification(notification: Notifications.Notification): string | null {
  return extractRequestIdFromNotification(
    notification.request.content.data as Record<string, unknown>
  )
}

function configureNotificationPresentation(): void {
  if (notificationHandlerConfigured) return
  Notifications.setNotificationHandler({
    handleNotification: async () => ({
      shouldShowBanner: true,
      shouldShowList: true,
      shouldPlaySound: true,
      shouldSetBadge: false,
    }),
  })
  notificationHandlerConfigured = true
}

/** Route foreground deliveries, taps, and cold starts to consent review. */
export function installConsentNotificationListeners(
  navigate: ConsentNotificationNavigator
): () => void {
  configureNotificationPresentation()
  let active = true
  let pendingRequestId: string | null = null

  const navigateWhenAuthenticated = (requestId: string) => {
    const auth = getPatientAuthSnapshot()
    if (!auth.hydrated || auth.status !== 'authenticated') {
      pendingRequestId = requestId
      return
    }
    pendingRequestId = null
    if (active) navigate(requestId)
  }

  const unsubscribeAuth = subscribeToPatientAuth(() => {
    const auth = getPatientAuthSnapshot()
    if (active && pendingRequestId && auth.hydrated && auth.status === 'authenticated') {
      const requestId = pendingRequestId
      pendingRequestId = null
      navigate(requestId)
    }
  })

  const navigateFromNotification = (notification: Notifications.Notification) => {
    const requestId = requestIdFromNotification(notification)
    if (active && requestId) navigateWhenAuthenticated(requestId)
  }

  const receivedSubscription =
    Notifications.addNotificationReceivedListener(navigateFromNotification)
  const responseSubscription = Notifications.addNotificationResponseReceivedListener((response) =>
    navigateFromNotification(response.notification)
  )

  void Notifications.getLastNotificationResponseAsync()
    .then(async (response) => {
      if (!response) return
      navigateFromNotification(response.notification)
      await Notifications.clearLastNotificationResponseAsync()
    })
    .catch((error) => {
      if (process.env.NODE_ENV !== 'production') {
        console.warn('PUSH_COLD_START_ERROR', {
          cause: error instanceof Error ? error.name : 'UnknownError',
        })
      }
    })

  return () => {
    active = false
    pendingRequestId = null
    unsubscribeAuth()
    receivedSubscription.remove()
    responseSubscription.remove()
  }
}
