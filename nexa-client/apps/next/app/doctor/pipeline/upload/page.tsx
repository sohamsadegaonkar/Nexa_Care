'use client'
import { Suspense } from 'react'
import { PipelineUploadScreen } from 'app/features/pipeline/PipelineUploadScreen'

export default function Page() {
  return (
    <Suspense>
      <PipelineUploadScreen />
    </Suspense>
  )
}
