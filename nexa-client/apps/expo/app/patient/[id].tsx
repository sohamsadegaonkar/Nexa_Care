import { ProfileScreen } from 'app/features/patient/ProfileScreen'
import { Stack, useLocalSearchParams } from 'expo-router'

export default function Screen() {
  const { id, consentToken, purpose } = useLocalSearchParams<{
    id: string
    consentToken?: string
    purpose?: string
  }>()

  return (
    <>
      <Stack.Screen
        options={{
          title: 'Patient',
          presentation: 'modal',
          animation: 'slide_from_right',
          gestureEnabled: true,
          gestureDirection: 'horizontal',
        }}
      />
      <ProfileScreen
        patientId={id as string}
        consentToken={consentToken}
        purpose={purpose}
      />
    </>
  )
}
