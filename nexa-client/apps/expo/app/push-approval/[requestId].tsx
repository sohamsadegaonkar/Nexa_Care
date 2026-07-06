import { PatientApprovalScreen } from 'app/features/approval/PatientApprovalScreen'
import { Stack, useLocalSearchParams } from 'expo-router'

export default function ApprovalPage() {
  const { requestId } = useLocalSearchParams<{ requestId: string }>()

  return (
    <>
      <Stack.Screen options={{ title: 'Access Approval', headerShown: false }} />
      <PatientApprovalScreen requestId={requestId} />
    </>
  )
}
