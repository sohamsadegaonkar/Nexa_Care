'use client'

import { ActionButton, AuthFrame, InlineNotice, ScreenHeader } from '@my/ui'
import { useRouter } from 'next/navigation'

export default function PatientEntryPage() {
  const router = useRouter()
  return (
    <AuthFrame>
      <ScreenHeader
        title="Your patient account"
        description="Continue in the Nexa Care mobile app to sign in, review access requests, and see your access history."
      />
      <InlineNotice title="Use your enrolled phone">
        Patient approval uses the device security and biometrics in the mobile app. If you need help
        getting the app, contact your care organization.
      </InlineNotice>
      <ActionButton onPress={() => router.push('/doctor/login')}>
        Back to provider sign in
      </ActionButton>
    </AuthFrame>
  )
}
