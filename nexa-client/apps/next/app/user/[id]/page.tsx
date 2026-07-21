'use client'

import { ProfileScreen } from 'app/features/patient/ProfileScreen'
import { useParams, useSearchParams } from 'next/navigation'

export default function Page() {
  const params = useParams<{ id: string }>()
  const searchParams = useSearchParams()

  return <ProfileScreen patientId={params.id} workflowId={searchParams.get('workflow_id')} />
}