'use client'

import { Suspense } from 'react'
import { PatientRecordViewerScreen } from 'app/features/doctor/PatientRecordViewerScreen'

export default function Page() {
  return (
    <Suspense>
      <PatientRecordViewerScreen />
    </Suspense>
  )
}
