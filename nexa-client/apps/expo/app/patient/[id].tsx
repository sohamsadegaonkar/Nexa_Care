import { ProfileScreen } from 'app/features/patient/ProfileScreen'
import { Stack, useLocalSearchParams } from 'expo-router'

export default function Screen() {
  const { id, workflow_id } = useLocalSearchParams<{
    id: string
    workflow_id?: string
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
      <ProfileScreen patientId={id as string} workflowId={workflow_id ?? null} />
    </>
  )
}