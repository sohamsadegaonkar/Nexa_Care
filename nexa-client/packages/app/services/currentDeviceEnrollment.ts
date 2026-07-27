import * as SecureStore from 'expo-secure-store'
import { Platform } from 'react-native'
import { ApiError, getAuthToken } from '../utils/apiClient'
import {
  DEVICE_PRIVATE_KEY_STORAGE_KEY,
  type DeviceEnrollmentStage,
  type EnrollDeviceResponse,
  deleteDeviceKey,
  enrollDevice,
  fingerprintDevicePublicKey,
  generateDeviceKeypair,
  getDeviceId,
  getDeviceLabel,
  getDevices,
  getStoredDevicePublicKey,
  setDeviceId,
} from './deviceKeys'
import { clearPatientAuthSession, DEVICE_ENROLLMENT_TOKEN_STORAGE_KEY } from './patientAuthSession'

export type CurrentDeviceErrorCode =
  | 'SETUP_REQUIRED'
  | 'REAUTH_REQUIRED'
  | 'DEVICE_CONFLICT'
  | 'INVALID_ENROLLMENT'
  | 'NETWORK_ERROR'

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
  keyAlias: string
  publicKeyDerBase64: string | null
  keyFingerprint: string | null
  platform: 'ios' | 'android'
  hasPrivateKey: boolean
}

export interface CurrentDeviceEnrollment {
  deviceId: string
  status: 'active'
  enrolledNow: boolean
  keyFingerprint: string
}

export interface EnsureCurrentDeviceOptions {
  allowEnrollment?: boolean
  deviceLabel?: string
  expoPushToken?: string | null
  onStage?: (stage: DeviceEnrollmentStage) => void
}

let enrollmentInFlight: Promise<EnrollDeviceResponse> | null = null

export async function getLocalInstallationMetadata(): Promise<LocalInstallationMetadata> {
  const [deviceId, key] = await Promise.all([getDeviceId(), getStoredDevicePublicKey()])
  const publicKeyDerBase64 = key?.publicKeyDerBase64 ?? null
  return {
    deviceId,
    keyAlias: DEVICE_PRIVATE_KEY_STORAGE_KEY,
    publicKeyDerBase64,
    keyFingerprint: publicKeyDerBase64
      ? await fingerprintDevicePublicKey(publicKeyDerBase64)
      : null,
    platform: Platform.OS === 'ios' ? 'ios' : 'android',
    hasPrivateKey: publicKeyDerBase64 !== null,
  }
}

function mapError(error: unknown): CurrentDeviceError {
  if (error instanceof CurrentDeviceError) return error
  if (error instanceof ApiError) {
    if (error.status === 401 || error.code === 'REAUTH_REQUIRED') {
      return new CurrentDeviceError(
        'Your session or device enrollment authorization expired. Sign in with OTP again.',
        'REAUTH_REQUIRED',
        401
      )
    }
    if (error.status === 409) {
      return new CurrentDeviceError(
        'This device could not be enrolled. Revoke an unused device or retry after the current enrollment finishes.',
        'DEVICE_CONFLICT',
        409
      )
    }
    if (error.status === 400 || error.status === 422) {
      return new CurrentDeviceError(
        'The device enrollment request was rejected. Sign in again before retrying setup.',
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
  metadata: LocalInstallationMetadata,
  options: EnsureCurrentDeviceOptions
): Promise<EnrollDeviceResponse> {
  if (enrollmentInFlight) return enrollmentInFlight
  enrollmentInFlight = (async () => {
    const enrollmentToken = await SecureStore.getItemAsync(DEVICE_ENROLLMENT_TOKEN_STORAGE_KEY)
    if (!enrollmentToken) {
      throw new CurrentDeviceError(
        'Secure this device by signing in with a fresh OTP.',
        'REAUTH_REQUIRED',
        401
      )
    }
    options.onStage?.('generating')
    const key = metadata.publicKeyDerBase64
      ? { publicKeyDerBase64: metadata.publicKeyDerBase64 }
      : await generateDeviceKeypair()
    options.onStage?.('enrolling')
    const enrollment = await enrollDevice({
      device_public_key: key.publicKeyDerBase64,
      device_label: options.deviceLabel ?? getDeviceLabel(),
      platform: metadata.platform,
      device_enrollment_token: enrollmentToken,
      ...(options.expoPushToken ? { expo_push_token: options.expoPushToken } : {}),
    })
    await setDeviceId(enrollment.device_id)
    await SecureStore.deleteItemAsync(DEVICE_ENROLLMENT_TOKEN_STORAGE_KEY).catch(() => undefined)
    return enrollment
  })().finally(() => {
    enrollmentInFlight = null
  })
  return enrollmentInFlight
}

/**
 * Require this exact app installation to have both its private key and an
 * active matching server device_id. Another patient device never satisfies it.
 */
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
    let metadata = await getLocalInstallationMetadata()
    const server = await getDevices()
    const exactActiveDevice = metadata.deviceId
      ? server.devices.find(
          (device) =>
            device.device_id === metadata.deviceId &&
            device.status === 'active' &&
            device.public_key_fingerprint === metadata.keyFingerprint
        )
      : undefined

    if (exactActiveDevice && metadata.hasPrivateKey && metadata.keyFingerprint) {
      await SecureStore.deleteItemAsync(DEVICE_ENROLLMENT_TOKEN_STORAGE_KEY).catch(() => undefined)
      return {
        deviceId: exactActiveDevice.device_id,
        status: 'active',
        enrolledNow: false,
        keyFingerprint: metadata.keyFingerprint,
      }
    }

    if (options.allowEnrollment === false) {
      throw new CurrentDeviceError(
        'Secure this device to approve consent requests.',
        'SETUP_REQUIRED'
      )
    }

    if (!metadata.hasPrivateKey && metadata.deviceId) {
      await deleteDeviceKey()
      metadata = { ...metadata, deviceId: null }
    }
    const enrollment = await enrollInstallation(metadata, options)
    const enrolledMetadata = await getLocalInstallationMetadata()
    if (!enrolledMetadata.keyFingerprint) {
      throw new CurrentDeviceError(
        'The local signing key is unavailable after enrollment.',
        'SETUP_REQUIRED'
      )
    }
    return {
      deviceId: enrollment.device_id,
      status: 'active',
      enrolledNow: true,
      keyFingerprint: enrolledMetadata.keyFingerprint,
    }
  } catch (error) {
    const mapped = mapError(error)
    if (mapped.code === 'REAUTH_REQUIRED') await clearPatientAuthSession('expired')
    throw mapped
  }
}
