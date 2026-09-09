/**
 * Public device-authority contract for the patient client.
 *
 * Slice 6H deliberately keeps private-key operations out of this module. New enrollment,
 * rotation, recovery, and consent signing use native key aliases through nativeDeviceSecurity.
 * The old SecureStore scalar key name remains exported only so legacyDeviceKey.ts can perform
 * one-time proof-of-possession migration of pre-6H installations.
 */
import * as LocalAuthentication from 'expo-local-authentication'
import * as SecureStore from 'expo-secure-store'
import { Platform } from 'react-native'
import { apiClient } from '../utils/apiClient'

export {
  DEVICE_ENROLLMENT_TOKEN_STORAGE_KEY,
  PATIENT_ACCESS_TOKEN_STORAGE_KEY,
  configurePatientAuthTokenProvider,
  storePatientAuthSession,
} from './patientAuthSession'

/** Legacy pre-6H raw-scalar storage key. Do not use for new signing authority. */
export const DEVICE_PRIVATE_KEY_STORAGE_KEY = 'nexa_device_private_key_v1'
export const DEVICE_ID_STORAGE_KEY = 'nexa_device_id_v1'

export type DeviceEnrollmentStage = 'generating' | 'enrolling'

export interface DeviceKeyResult {
  /** Base64-encoded DER X.509 SubjectPublicKeyInfo; never private material. */
  publicKeyDerBase64: string
}

export interface EnrollDeviceParams {
  device_public_key: string
  device_label: string
  platform: string
  device_enrollment_token: string
  expo_push_token?: string
}

export interface EnrollDeviceResponse {
  device_id: string
  key_id: string
  key_version: number
  status: string
  patient_id: string
  enrolled_at: string
}

export interface DeviceInfo {
  device_id: string
  key_id: string
  key_version: number
  device_label: string | null
  platform: string
  status: string
  enrolled_at: string
  public_key_fingerprint: string
}

export interface DevicesListResponse {
  patient_id: string
  devices: DeviceInfo[]
}

export function getDeviceLabel(): string {
  const deviceName = Platform.OS === 'ios' ? 'iPhone' : 'Android'
  return `${deviceName} — ${new Date().toLocaleDateString()}`
}

/** Send only the public key produced by the native key module. */
export async function enrollDevice(params: EnrollDeviceParams): Promise<EnrollDeviceResponse> {
  const { data } = await apiClient.post<EnrollDeviceResponse>(
    '/api/v2/patient/devices/enroll',
    params as unknown as Record<string, unknown>
  )
  return data
}

export async function getDevices(): Promise<DevicesListResponse> {
  const { data } = await apiClient.get<DevicesListResponse>('/api/v2/patient/devices')
  return data
}

/** Store only the server-issued logical device identifier; no signing key material is stored here. */
export async function setDeviceId(deviceId: string): Promise<void> {
  await SecureStore.setItemAsync(DEVICE_ID_STORAGE_KEY, deviceId, {
    keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
  })
}

export async function getDeviceId(): Promise<string | null> {
  return (await SecureStore.getItemAsync(DEVICE_ID_STORAGE_KEY)) ?? null
}

export interface ConsentSigningFields {
  request_id: string
  patient_id: string
  provider_id: string
  challenge_nonce: string
  decision: 'approved' | 'denied'
  scope: string
  purpose: string
  access_duration: number
  issued_at: string
  expires_at: string
  device_id: string
}

/** Frozen V2 canonicalization retained only for protocol compatibility during V3 transition. */
export function constructConsentSigningInput(params: ConsentSigningFields): string {
  return JSON.stringify({
    access_duration: params.access_duration,
    challenge_nonce: params.challenge_nonce,
    decision: params.decision,
    device_id: params.device_id,
    expires_at: params.expires_at,
    issued_at: params.issued_at,
    patient_id: params.patient_id,
    protocol_version: 'nexa-consent-v2',
    provider_id: params.provider_id,
    purpose: params.purpose,
    request_id: params.request_id,
    scope: params.scope,
  })
}

/** Local user-verification gate. The signing key operation itself remains in the native module. */
export async function authenticateWithBiometrics(): Promise<void> {
  if (
    !(await LocalAuthentication.hasHardwareAsync()) ||
    !(await LocalAuthentication.isEnrolledAsync())
  ) {
    throw new Error('Biometric authentication is not available on this device.')
  }
  const result = await LocalAuthentication.authenticateAsync({
    promptMessage: 'Confirm your identity to approve this request',
    fallbackLabel: 'Use Passcode',
    cancelLabel: 'Cancel',
  })
  if (!result.success) throw new Error('Biometric verification cancelled.')
}

export interface ConsentSigningFieldsV3 {
  request_id: string
  patient_id: string
  provider_id: string
  hospital_id: string
  challenge_nonce: string
  decision: 'approved' | 'denied'
  scope: string
  purpose: string
  access_duration: number
  issued_at: string
  expires_at: string
  consent_context_hash: string
  device_id: string
  key_id: string
  key_version: number
  public_key_fingerprint: string
}

export function constructConsentSigningInputV3(params: ConsentSigningFieldsV3): string {
  return JSON.stringify({
    access_duration: params.access_duration,
    challenge_nonce: params.challenge_nonce,
    consent_context_hash: params.consent_context_hash,
    decision: params.decision,
    device_id: params.device_id,
    domain: 'NEXA_CARE_SIGNED_CONSENT',
    expires_at: params.expires_at,
    hospital_id: params.hospital_id,
    issued_at: params.issued_at,
    key_id: params.key_id,
    key_version: params.key_version,
    operation: 'CONSENT_DECISION',
    patient_id: params.patient_id,
    protocol_version: 'nexa-consent-v3',
    provider_id: params.provider_id,
    public_key_fingerprint: params.public_key_fingerprint,
    purpose: params.purpose,
    request_id: params.request_id,
    scope: params.scope,
  })
}
