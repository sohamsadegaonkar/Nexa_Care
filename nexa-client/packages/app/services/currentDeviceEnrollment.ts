import * as SecureStore from 'expo-secure-store'
import { Platform } from 'react-native'
import { ApiError, getAuthToken } from '../utils/apiClient'
import {
  DEVICE_ID_STORAGE_KEY,
  type DeviceEnrollmentStage,
  type DeviceInfo,
  type EnrollDeviceResponse,
  enrollDevice,
  getDeviceId,
  getDeviceLabel,
  getDevices,
  setDeviceId,
} from './deviceKeys'
import { deleteLegacyDevicePrivateKey, getLegacyDeviceKeyInfo } from './legacyDeviceKey'
import {
  commitPendingNativeDeviceKey,
  ensurePendingNativeDeviceKey,
  fingerprintPublicKeyDerBase64,
  getCurrentNativeDeviceKey,
  getPendingNativeDeviceKey,
} from './nativeDeviceKeyring'
import type { NativeDeviceKeyCustody } from './nativeDeviceSecurity'
import { clearPatientAuthSession, DEVICE_ENROLLMENT_TOKEN_STORAGE_KEY } from './patientAuthSession'
import { migrateLegacyDeviceToNative } from './patientDeviceRotation'

export type CurrentDeviceErrorCode =
  | 'SETUP_REQUIRED'
  | 'REAUTH_REQUIRED'
  | 'RECOVERY_REQUIRED'
  | 'DEVICE_CONFLICT'
  | 'INVALID_ENROLLMENT'
  | 'NETWORK_ERROR'
  | 'NATIVE_SECURITY_REQUIRED'

export class CurrentDeviceError extends Error {
  constructor(
    message: string,
    public readonly code: CurrentDeviceErrorCode,
    public readonly status = 0,
    public readonly retryable = false
  ) {
    super(message)
    this.name = 'CurrentDeviceError'
  }
}

export interface LocalInstallationMetadata {
  deviceId: string | null
  publicKeyDerBase64: string | null
  keyFingerprint: string | null
  keyAlias: string | null
  custody: NativeDeviceKeyCustody | 'legacy-secure-store' | null
  platform: 'ios' | 'android'
  hasPrivateKey: boolean
}

export interface CurrentDeviceEnrollment {
  deviceId: string
  keyId: string
  keyVersion: number
  status: 'active'
  enrolledNow: boolean
  keyFingerprint: string
  keyAlias: string
  custody: NativeDeviceKeyCustody
}

export interface EnsureCurrentDeviceOptions {
  allowEnrollment?: boolean
  deviceLabel?: string
  expoPushToken?: string | null
  onStage?: (stage: DeviceEnrollmentStage) => void
}

let enrollmentInFlight: Promise<EnrollDeviceResponse> | null = null
let migrationInFlight: Promise<CurrentDeviceEnrollment> | null = null

export async function getLocalInstallationMetadata(): Promise<LocalInstallationMetadata> {
  const deviceId = await getDeviceId()
  const native = await getCurrentNativeDeviceKey()
  if (native) {
    return {
      deviceId,
      publicKeyDerBase64: native.publicKeyDerBase64,
      keyFingerprint: await fingerprintPublicKeyDerBase64(native.publicKeyDerBase64),
      keyAlias: native.alias,
      custody: native.custody,
      platform: Platform.OS === 'ios' ? 'ios' : 'android',
      hasPrivateKey: true,
    }
  }
  const legacy = await getLegacyDeviceKeyInfo()
  return {
    deviceId,
    publicKeyDerBase64: legacy?.publicKeyDerBase64 ?? null,
    keyFingerprint: legacy?.publicKeyFingerprint ?? null,
    keyAlias: null,
    custody: legacy ? 'legacy-secure-store' : null,
    platform: Platform.OS === 'ios' ? 'ios' : 'android',
    hasPrivateKey: legacy !== null,
  }
}

function mapError(error: unknown): CurrentDeviceError {
  if (error instanceof CurrentDeviceError) return error
  if (
    error &&
    typeof error === 'object' &&
    'code' in error &&
    (error as { code?: unknown }).code === 'NATIVE_DEVICE_SECURITY_UNAVAILABLE'
  ) {
    return new CurrentDeviceError(
      'This build does not include Nexa Care native device-key protection. Install a development or production build.',
      'NATIVE_SECURITY_REQUIRED'
    )
  }
  if (error instanceof ApiError) {
    if (error.status === 401 || error.code === 'REAUTH_REQUIRED') {
      return new CurrentDeviceError(
        'Your session or device enrollment authorization expired. Sign in with OTP again.',
        'REAUTH_REQUIRED',
        401
      )
    }
    if (error.status === 409 && error.code === 'DEVICE_RECOVERY_REQUIRED') {
      return new CurrentDeviceError(
        'This account already has device history and this installation is not a current trusted device. Verify your identity to recover device access.',
        'RECOVERY_REQUIRED',
        409
      )
    }
    if (error.status === 409) {
      return new CurrentDeviceError(
        'This device operation conflicted with current device state. Refresh trusted devices before retrying.',
        'DEVICE_CONFLICT',
        409
      )
    }
    if (error.status === 400 || error.status === 422) {
      return new CurrentDeviceError(
        'The device request was rejected. Sign in again before retrying setup.',
        'INVALID_ENROLLMENT',
        error.status
      )
    }
    if (error.status === 0 || error.isRetryable) {
      return new CurrentDeviceError(
        'Unable to reach Nexa Care while securing this device. Check your connection and retry.',
        'NETWORK_ERROR',
        error.status,
        true
      )
    }
  }
  return new CurrentDeviceError(
    error instanceof Error ? error.message : 'Device enrollment failed.',
    'INVALID_ENROLLMENT'
  )
}

async function enrollInstallation(
  options: EnsureCurrentDeviceOptions,
  hasDeviceHistory: boolean
): Promise<EnrollDeviceResponse> {
  if (enrollmentInFlight) return enrollmentInFlight
  enrollmentInFlight = (async () => {
    const enrollmentToken = await SecureStore.getItemAsync(DEVICE_ENROLLMENT_TOKEN_STORAGE_KEY)
    if (!enrollmentToken) {
      if (hasDeviceHistory) {
        throw new CurrentDeviceError(
          'This installation is not a current trusted device. Authorize it from a trusted device, or use account recovery if all trusted devices are lost.',
          'RECOVERY_REQUIRED',
          409
        )
      }
      throw new CurrentDeviceError(
        'Secure this device by signing in with a fresh OTP.',
        'REAUTH_REQUIRED',
        401
      )
    }
    options.onStage?.('generating')
    const key = await ensurePendingNativeDeviceKey()
    options.onStage?.('enrolling')
    const enrollment = await enrollDevice({
      device_public_key: key.publicKeyDerBase64,
      device_label: options.deviceLabel ?? getDeviceLabel(),
      platform: Platform.OS === 'ios' ? 'ios' : 'android',
      device_enrollment_token: enrollmentToken,
      ...(options.expoPushToken ? { expo_push_token: options.expoPushToken } : {}),
    })
    await setDeviceId(enrollment.device_id)
    await commitPendingNativeDeviceKey(key.alias, { deletePrevious: true })
    await SecureStore.deleteItemAsync(DEVICE_ENROLLMENT_TOKEN_STORAGE_KEY).catch(() => undefined)
    return enrollment
  })().finally(() => {
    enrollmentInFlight = null
  })
  return enrollmentInFlight
}

async function resultForServerDevice(
  device: DeviceInfo,
  enrolledNow: boolean
): Promise<CurrentDeviceEnrollment> {
  const key = await getCurrentNativeDeviceKey()
  if (!key) throw new CurrentDeviceError('Native signing key is unavailable.', 'SETUP_REQUIRED')
  return {
    deviceId: device.device_id,
    keyId: device.key_id,
    keyVersion: device.key_version,
    status: 'active',
    enrolledNow,
    keyFingerprint: device.public_key_fingerprint,
    keyAlias: key.alias,
    custody: key.custody,
  }
}

async function reconcilePendingDevice(devices: DeviceInfo[]): Promise<CurrentDeviceEnrollment | null> {
  const pending = await getPendingNativeDeviceKey()
  if (!pending) return null
  const fingerprint = await fingerprintPublicKeyDerBase64(pending.publicKeyDerBase64)
  const match = devices.find(
    (device) => device.status === 'active' && device.public_key_fingerprint === fingerprint
  )
  if (!match) return null
  await commitPendingNativeDeviceKey(pending.alias, { deletePrevious: true })
  await setDeviceId(match.device_id)
  await deleteLegacyDevicePrivateKey().catch(() => undefined)
  return resultForServerDevice(match, false)
}

async function migrateLegacy(match: DeviceInfo): Promise<CurrentDeviceEnrollment> {
  if (migrationInFlight) return migrationInFlight
  migrationInFlight = (async () => {
    const { rotation } = await migrateLegacyDeviceToNative(match)
    await setDeviceId(rotation.device_id)
    const devices = await getDevices()
    const current = devices.devices.find(
      (device) =>
        device.device_id === rotation.device_id &&
        device.key_id === rotation.new_key_id &&
        device.status === 'active'
    )
    if (!current) throw new Error('DEVICE_ROTATION_RECONCILIATION_FAILED')
    return resultForServerDevice(current, false)
  })().finally(() => {
    migrationInFlight = null
  })
  return migrationInFlight
}

export async function ensureCurrentDeviceEnrollment(
  options: EnsureCurrentDeviceOptions = {}
): Promise<CurrentDeviceEnrollment> {
  if (!(await getAuthToken())) {
    const error = new CurrentDeviceError(
      'Your patient session expired. Sign in with OTP again.',
      'REAUTH_REQUIRED',
      401
    )
    await clearPatientAuthSession('expired')
    throw error
  }

  try {
    const server = await getDevices()
    const reconciledPending = await reconcilePendingDevice(server.devices)
    if (reconciledPending) return reconciledPending

    const metadata = await getLocalInstallationMetadata()
    if (metadata.custody !== 'legacy-secure-store' && metadata.keyAlias && metadata.keyFingerprint) {
      const exact = metadata.deviceId
        ? server.devices.find(
            (device) =>
              device.device_id === metadata.deviceId &&
              device.status === 'active' &&
              device.public_key_fingerprint === metadata.keyFingerprint
          )
        : undefined
      if (exact) {
        await SecureStore.deleteItemAsync(DEVICE_ENROLLMENT_TOKEN_STORAGE_KEY).catch(() => undefined)
        return resultForServerDevice(exact, false)
      }
      const fingerprintMatch = server.devices.find(
        (device) =>
          device.status === 'active' && device.public_key_fingerprint === metadata.keyFingerprint
      )
      if (fingerprintMatch) {
        await setDeviceId(fingerprintMatch.device_id)
        await SecureStore.deleteItemAsync(DEVICE_ENROLLMENT_TOKEN_STORAGE_KEY).catch(() => undefined)
        return resultForServerDevice(fingerprintMatch, false)
      }
    }

    if (metadata.custody === 'legacy-secure-store' && metadata.keyFingerprint) {
      const legacyMatch = server.devices.find(
        (device) =>
          device.status === 'active' && device.public_key_fingerprint === metadata.keyFingerprint
      )
      if (legacyMatch) return migrateLegacy(legacyMatch)
      if (server.devices.length === 0) await deleteLegacyDevicePrivateKey()
    }

    if (server.devices.length > 0) {
      throw new CurrentDeviceError(
        'This installation is not a current trusted device. Authorize it from a trusted device, or use account recovery if all trusted devices are lost.',
        'RECOVERY_REQUIRED',
        409
      )
    }
    if (options.allowEnrollment === false) {
      throw new CurrentDeviceError('Secure this device to approve consent requests.', 'SETUP_REQUIRED')
    }

    if (metadata.deviceId) {
      await SecureStore.deleteItemAsync(DEVICE_ID_STORAGE_KEY).catch(() => undefined)
    }
    const enrollment = await enrollInstallation(options, false)
    const freshServer = await getDevices()
    const enrolled = freshServer.devices.find(
      (device) => device.device_id === enrollment.device_id && device.status === 'active'
    )
    if (!enrolled) throw new Error('DEVICE_ENROLLMENT_RECONCILIATION_FAILED')
    return resultForServerDevice(enrolled, true)
  } catch (error) {
    const mapped = mapError(error)
    if (mapped.code === 'REAUTH_REQUIRED') await clearPatientAuthSession('expired')
    throw mapped
  }
}
