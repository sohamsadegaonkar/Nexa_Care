'use client'

import PatientImportScreen from 'app/features/patient/PatientImportScreen'
import { useSearchParams } from 'next/navigation'
import React, { Suspense } from 'react'

function PatientImportContent() {
  const searchParams = useSearchParams()
  const importId = searchParams.get('import_id')
  return <PatientImportScreen initialImportId={importId} />
}

export default function PatientImportPage() {
  return (
    <Suspense fallback={<PatientImportScreen />}>
      <PatientImportContent />
    </Suspense>
  )
}
