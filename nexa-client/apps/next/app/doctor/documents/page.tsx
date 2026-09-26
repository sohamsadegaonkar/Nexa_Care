'use client'
import { Suspense } from 'react'
import { DocumentsWorkspaceScreen } from 'app/features/doctor/DocumentsWorkspaceScreen'

export default function Page() {
  return (
    <Suspense>
      <DocumentsWorkspaceScreen />
    </Suspense>
  )
}
