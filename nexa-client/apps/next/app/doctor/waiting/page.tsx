'use client'

import { Suspense } from 'react'
import { WaitingForApprovalScreen } from 'app/features/doctor/WaitingForApprovalScreen'

export default function Page() {
  return (
    <Suspense>
      <WaitingForApprovalScreen />
    </Suspense>
  )
}
