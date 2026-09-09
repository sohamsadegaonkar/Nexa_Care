import * as SecureStore from 'expo-secure-store'
import { NexaApiClient } from '../utils/apiClient'
import {
  DEVICE_ID_STORAGE_KEY,
  getDeviceId,
  getDevices,
  type DeviceInfo,
} from './deviceKeys'
import { clearCurrentNativeDeviceKey } from './nativeDeviceKeyring'
import { rotateCurrentNativeDeviceKey } from './patientDeviceRotation'

export interface ManagedDevice extends DeviceInfo {
  isCurrentInstallation: boolean
}

export async function listManagedPatientDevices(): Promise<ManagedDevice[]> {
  const [server, localDeviceId] = await Promise.all([getDevices(), getDeviceId()])
  return server.devices.map((device) => ({
    ...device,
    isCurrentInstallation: device.device_id === localDeviceId,
  }))
}

/**
 * Revoke server authority first. Local native-key/device-id cleanup is allowed only after the
 * server confirms the revocation, so a transport failure never creates a misleading local logout
 * while the server still considers the device active.
 */
export async function revokeManagedPatientDevice(deviceId: string): Promise<void> {
  const localDeviceId = await getDeviceId()
  const response = await NexaApiClient.revokeDevice(deviceId)
  if (response.device_id !== deviceId || response.status !== 'revoked') {
    throw new Error('DEVICE_REVOKE_RESPONSE_BINDING_MISMATCH')
  }
  if (localDeviceId === deviceId) {
    await clearCurrentNativeDeviceKey()
    await SecureStore.deleteItemAsync(DEVICE_ID_STORAGE_KEY).catch(() => undefined)
  }
}

/** Rotate only this installation's active logical device through the qualified 6D PoP contract. */
export async function rotateCurrentManagedDevice(): Promise<ManagedDevice> {
  const devices = await listManagedPatientDevices()
  const current = devices.find(
    (device) => device.isCurrentInstallation && device.status === 'active'
  )
  if (!current) throw new Error('CURRENT_MANAGED_DEVICE_NOT_ACTIVE')
  const { rotation } = await rotateCurrentNativeDeviceKey(current)
  const refreshed = await listManagedPatientDevices()
  const rotated = refreshed.find(
    (device) =>
      device.device_id === rotation.device_id &&
      device.key_id === rotation.new_key_id &&
      device.key_version === rotation.new_key_version &&
      device.status === 'active'
  )
  if (!rotated) throw new Error('DEVICE_ROTATION_RECONCILIATION_FAILED')
  return rotated
}
