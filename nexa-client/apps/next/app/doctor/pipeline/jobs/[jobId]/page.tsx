'use client'
import { Suspense } from 'react'
import { JobStatusScreen } from 'app/features/pipeline/JobStatusScreen'

export default function Page() {
  return (
    <Suspense>
      <JobStatusScreen />
    </Suspense>
  )
}
