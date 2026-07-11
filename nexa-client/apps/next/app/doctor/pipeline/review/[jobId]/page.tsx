'use client'
import { Suspense } from 'react'
import { ReviewCockpitScreen } from 'app/features/pipeline/ReviewCockpitScreen'

export default function Page() {
  return (
    <Suspense>
      <ReviewCockpitScreen />
    </Suspense>
  )
}
