/**
 * Push notification registration and handling for Nexa Care patient app.
 *
 * Registers the Expo push token with the backend so that consent request
 * notifications can be sent to the patient's device.  When a notification
 * is tapped, navigates to the consent request screen.
 *
 * ALPHA: Not yet verified on device.  Test on a real device before demo.
 */

import { Platform } from 'react-native'
import { apiClient } from '../utils/api'

// ── Types ────────────────────────────────────────────────────────────────────

export interface PushTokenRegistration {
  expo_push_token: string
  platform: 'ios' | 'android'
}

// ── Push registration ────────────────────────────────────────────────────────

/**
 * Register for push notifications and send the token to the backend.
 *
 * In a real Expo build, this would call:
 *   - Notifications.requestPermissionsAsync()
 *   - Notifications.getExpoPushTokenAsync()
 *   - POST /api/v2/push/register-token
 *
 * ALPHA: The Expo Notifications API is not available in the sandbox.
 * This function is structured for real-device integration.
 */
export async function registerForPushNotifications(): Promise<string | null> {
  // In production Expo build:
  // const { status } = await Notifications.requestPermissionsAsync()
  // if (status !== 'granted') return null
  // const token = (await Notifications.getExpoPushTokenAsync()).data

  // For now, return null — the actual token will come from the
  // Expo notifications API on a real device.
  return null
}

/**
 * Register an Expo push token with the backend via shared apiClient.
 */
export async function registerPushToken(
  expoPushToken: string,
): Promise<void> {
  await apiClient.post(
    '/api/v2/push/register-token',
    {
      expo_push_token: expoPushToken,
      platform: Platform.OS === 'ios' ? 'ios' : 'android',
    } as unknown as Record<string, unknown>,
  )
}

/**
 * Handle a notification tap by returning the request_id from the
 * notification payload.  The calling screen uses this to navigate
 * to the consent request screen.
 *
 * In a real Expo build, this would be called from the
 * Notifications.addNotificationResponseReceivedListener callback.
 */
export function extractRequestIdFromNotification(
  notificationData: Record<string, unknown>,
): string | null {
  const requestId = notificationData?.request_id
    ?? (notificationData?.data as Record<string, unknown>)?.request_id
    ?? null
  return typeof requestId === 'string' ? requestId : null
}
