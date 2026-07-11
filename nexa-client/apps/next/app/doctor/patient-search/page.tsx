'use client'
import { Suspense } from 'react'
import { PatientSearchScreen } from 'app/features/doctor/PatientSearchScreen'

export default function Page() {
  return (
    <Suspense>
      <PatientSearchScreen />
    </Suspense>
  )
}
