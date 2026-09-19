'use client'

import { Suspense } from 'react'
import PatientTreatmentAccessScreen from 'app/features/patient/PatientTreatmentAccessScreen'
import { Spinner, YStack } from 'tamagui'

export default function PatientTreatmentAccessPage() {
  return (
    <Suspense
      fallback={
        <YStack flex={1} alignItems="center" justifyContent="center" minHeight={300}>
          <Spinner size="large" color="$blue10" />
        </YStack>
      }
    >
      <PatientTreatmentAccessScreen />
    </Suspense>
  )
}
