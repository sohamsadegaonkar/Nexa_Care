'use client'
import { Suspense } from 'react'
import { CommitScreen } from 'app/features/pipeline/CommitScreen'

export default function Page() {
  return (
    <Suspense>
      <CommitScreen />
    </Suspense>
  )
}
