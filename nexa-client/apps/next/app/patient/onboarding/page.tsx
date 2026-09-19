'use client'

import { Suspense } from 'react'
import PatientOnboardingScreen from 'app/features/patient/PatientOnboardingScreen'
import { Spinner, YStack } from 'tamagui'

export default function PatientOnboardingPage() {
  return (
    <Suspense
      fallback={
        <YStack flex={1} alignItems="center" justifyContent="center" minHeight={300}>
          <Spinner size="large" color="$blue10" />
        </YStack>
      }
    >
      <PatientOnboardingScreen />
    </Suspense>
  )
}
