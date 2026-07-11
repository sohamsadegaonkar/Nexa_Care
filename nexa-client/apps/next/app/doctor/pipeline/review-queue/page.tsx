'use client'
import { Suspense } from 'react'
import { ReviewQueueScreen } from 'app/features/pipeline/ReviewQueueScreen'

export default function Page() {
  return (
    <Suspense>
      <ReviewQueueScreen />
    </Suspense>
  )
}
