'use client'

import { Suspense } from 'react'
import { RequestConsentScreen } from 'app/features/doctor/RequestConsentScreen'

export default function Page() {
  return (
    <Suspense>
      <RequestConsentScreen />
    </Suspense>
  )
}
