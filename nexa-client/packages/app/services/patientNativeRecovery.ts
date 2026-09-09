import { Platform } from 'react-native'
import { setDeviceId } from './deviceKeys'
import { deleteLegacyDevicePrivateKey } from './legacyDeviceKey'
import {
  commitPendingNativeDeviceKey,
  ensurePendingNativeDeviceKey,
  fingerprintPublicKeyDerBase64,
} from './nativeDeviceKeyring'
import { storePatientAuthSession } from './patientAuthSession'
import {
  completePatientDeviceRecovery,
  type RecoveryCompleteResponse,
} from './patientRecovery'

export interface CompleteNativeRecoveryParams {
  recoveryToken: string
  deviceLabel: string
}

/**
 * Install fresh patient device authority after identity recovery.
 *
 * The recovery capability is sent with a newly generated native public key. The private key is
 * referenced only by its local native alias. After the server commits recovery, the returned
 * authority is bound back to the exact public-key fingerprint before the alias becomes current.
 */
export async function completeNativePatientRecovery(
  params: CompleteNativeRecoveryParams
): Promise<RecoveryCompleteResponse> {
  const pending = await ensurePendingNativeDeviceKey()
  const expectedFingerprint = await fingerprintPublicKeyDerBase64(pending.publicKeyDerBase64)

  const response = await completePatientDeviceRecovery({
    recovery_token: params.recoveryToken,
    new_device_public_key: pending.publicKeyDerBase64,
    device_label: params.deviceLabel,
    platform: Platform.OS === 'ios' ? 'ios' : 'android',
  })

  if (
    response.status !== 'active' ||
    response.key_version !== 1 ||
    response.public_key_fingerprint !== expectedFingerprint ||
    !response.device_id ||
    !response.key_id ||
    !response.access_token
  ) {
    throw new Error('DEVICE_RECOVERY_RESPONSE_BINDING_MISMATCH')
  }

  // Persist the server-issued replacement session before promoting the pending alias. If local
  // promotion is interrupted, the authenticated client can reconcile the still-pending alias
  // against the server's active fingerprint on the next device-authority check.
  await storePatientAuthSession(response.access_token, null)
  await setDeviceId(response.device_id)
  await commitPendingNativeDeviceKey(pending.alias, { deletePrevious: true })
  await deleteLegacyDevicePrivateKey().catch(() => undefined)
  return response
}
