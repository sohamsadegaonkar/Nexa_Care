'use client'

import PatientImportScreen from 'app/features/patient/PatientImportScreen'
import { useSearchParams } from 'next/navigation'
import React, { Suspense } from 'react'

function PatientImportContent() {
  const searchParams = useSearchParams()
  const importId = searchParams.get('import_id')
  const returnTo = searchParams.get('returnTo')
  return <PatientImportScreen initialImportId={importId} returnTo={returnTo} />
}

export default function PatientImportPage() {
  return (
    <Suspense fallback={<PatientImportScreen />}>
      <PatientImportContent />
    </Suspense>
  )
}
