import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'expo-router'
import { Button, H2, Paragraph, ScrollView, Spinner, Text, XStack, YStack } from 'tamagui'
import {
  listManagedPatientDevices,
  revokeManagedPatientDevice,
  rotateCurrentManagedDevice,
  type ManagedDevice,
} from '../../services/patientDeviceManagement'

function compactFingerprint(value: string): string {
  return value.length > 16 ? `${value.slice(0, 8)}…${value.slice(-8)}` : value
}

export default function TrustedDevicesScreen() {
  const router = useRouter()
  const [devices, setDevices] = useState<ManagedDevice[]>([])
  const [loading, setLoading] = useState(true)
  const [busyDeviceId, setBusyDeviceId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setDevices(await listManagedPatientDevices())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to load trusted devices.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const revoke = async (device: ManagedDevice) => {
    setBusyDeviceId(device.device_id)
    setError(null)
    try {
      await revokeManagedPatientDevice(device.device_id)
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to revoke this device.')
    } finally {
      setBusyDeviceId(null)
    }
  }

  const rotate = async () => {
    const current = devices.find((device) => device.isCurrentInstallation)
    if (!current) return
    setBusyDeviceId(current.device_id)
    setError(null)
    try {
      await rotateCurrentManagedDevice()
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to rotate this device key.')
    } finally {
      setBusyDeviceId(null)
    }
  }

  return (
    <ScrollView flex={1} backgroundColor="$background">
      <YStack padding="$4" gap="$4">
        <XStack alignItems="center" justifyContent="space-between" gap="$3">
          <YStack flex={1} gap="$1">
            <H2>Trusted Devices</H2>
            <Paragraph color="$color10">
              Review device authority, rotate this installation&apos;s signing key, or revoke a device you no longer trust.
            </Paragraph>
          </YStack>
          <Button size="$3" onPress={() => router.back()}>Back</Button>
        </XStack>

        {loading ? <Spinner size="large" /> : null}
        {error ? <Text color="$red10">{error}</Text> : null}

        {!loading && devices.length === 0 ? (
          <Paragraph color="$color10">No device history is available.</Paragraph>
        ) : null}

        {devices.map((device) => {
          const busy = busyDeviceId === device.device_id
          return (
            <YStack key={`${device.device_id}:${device.key_id}`} borderWidth={1} borderColor="$borderColor" borderRadius="$4" padding="$4" gap="$2">
              <XStack justifyContent="space-between" gap="$3" alignItems="center">
                <YStack flex={1}>
                  <Text fontWeight="700">{device.device_label || `${device.platform} device`}</Text>
                  <Text color="$color10" fontSize="$2">
                    {device.isCurrentInstallation ? 'This installation · ' : ''}{device.status}
                  </Text>
                </YStack>
                <Text fontSize="$2">v{device.key_version}</Text>
              </XStack>
              <Text color="$color10" fontSize="$2">Key {compactFingerprint(device.public_key_fingerprint)}</Text>
              <Text color="$color10" fontSize="$2">Enrolled {new Date(device.enrolled_at).toLocaleString()}</Text>

              {device.status === 'active' ? (
                <XStack gap="$2" flexWrap="wrap">
                  {device.isCurrentInstallation ? (
                    <Button size="$3" disabled={busy} onPress={rotate}>
                      {busy ? 'Working…' : 'Rotate signing key'}
                    </Button>
                  ) : null}
                  <Button theme="red" size="$3" disabled={busy} onPress={() => revoke(device)}>
                    {busy ? 'Working…' : device.isCurrentInstallation ? 'Revoke this device' : 'Revoke device'}
                  </Button>
                </XStack>
              ) : null}
            </YStack>
          )
        })}

        <Paragraph color="$color10" fontSize="$2">
          Revocation is confirmed by Nexa Care before this app removes the local native key. Hardware execution is qualified separately from this management UI.
        </Paragraph>
      </YStack>
    </ScrollView>
  )
}
