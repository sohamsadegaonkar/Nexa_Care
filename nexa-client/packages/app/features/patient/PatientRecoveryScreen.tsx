import { useState } from 'react'
import { useRouter } from 'expo-router'
import { Button, H2, Input, Paragraph, Spinner, Text, YStack } from 'tamagui'
import { getDeviceLabel } from '../../services/deviceKeys'
import { completeNativePatientRecovery } from '../../services/patientNativeRecovery'
import { requestPatientRecoveryOtp, verifyPatientRecoveryOtp } from '../../services/patientRecovery'

export default function PatientRecoveryScreen() {
  const router = useRouter()
  const [phone, setPhone] = useState('')
  const [otp, setOtp] = useState('')
  const [stage, setStage] = useState<'phone' | 'otp' | 'recovering'>('phone')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const sendOtp = async () => {
    setBusy(true)
    setError(null)
    try {
      await requestPatientRecoveryOtp(phone.trim())
      setStage('otp')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to send recovery OTP.')
    } finally {
      setBusy(false)
    }
  }

  const recover = async () => {
    setBusy(true)
    setError(null)
    setStage('recovering')
    try {
      const capability = await verifyPatientRecoveryOtp(phone.trim(), otp.trim())
      if (capability.operation !== 'recover_patient_device_authority') {
        throw new Error('RECOVERY_CAPABILITY_OPERATION_MISMATCH')
      }
      const recovered = await completeNativePatientRecovery({
        recoveryToken: capability.recovery_token,
        deviceLabel: getDeviceLabel(),
      })
      router.replace({
        pathname: '/patient/enrolled',
        params: { deviceId: recovered.device_id, enrolledAt: new Date().toISOString() },
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to recover device authority.')
      setStage('otp')
    } finally {
      setBusy(false)
    }
  }

  return (
    <YStack flex={1} backgroundColor="$background" padding="$4" gap="$4" justifyContent="center">
      <YStack gap="$2">
        <H2>Recover Device Access</H2>
        <Paragraph color="$color10">
          Use account identity recovery only when your trusted device key is unavailable. Recovery creates fresh device authority; it never reconstructs the old private key.
        </Paragraph>
      </YStack>

      <YStack gap="$3">
        <Input
          accessibilityLabel="Recovery phone number"
          placeholder="Phone number"
          value={phone}
          readOnly={busy || stage !== 'phone'}
          onChangeText={setPhone}
          keyboardType="phone-pad"
        />

        {stage !== 'phone' ? (
          <Input
            accessibilityLabel="Recovery OTP"
            placeholder="OTP"
            value={otp}
            readOnly={busy || stage !== 'otp'}
            onChangeText={setOtp}
            keyboardType="number-pad"
          />
        ) : null}

        {stage === 'phone' ? (
          <Button disabled={busy || phone.trim().length < 6} onPress={sendOtp}>
            {busy ? 'Sending…' : 'Send recovery OTP'}
          </Button>
        ) : stage === 'otp' ? (
          <Button theme="blue" disabled={busy || otp.trim().length < 4} onPress={recover}>
            Recover with fresh device key
          </Button>
        ) : (
          <YStack gap="$2" alignItems="center">
            <Spinner size="large" />
            <Paragraph color="$color10">Replacing device authority…</Paragraph>
          </YStack>
        )}
      </YStack>

      {error ? <Text color="$red10">{error}</Text> : null}
      <Button chromeless disabled={busy} onPress={() => router.replace('/patient/login')}>
        Back to sign in
      </Button>
    </YStack>
  )
}
