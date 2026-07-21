'use client'

import { ProfileScreen } from 'app/features/patient/ProfileScreen'
import { useParams, useSearchParams } from 'next/navigation'

export default function Page() {
  const params = useParams<{ id: string }>()
  const searchParams = useSearchParams()
  const workflowId = searchParams.get('workflow_id')

  return (
    <ProfileScreen
      patientId={params.id}
      workflowId={workflowId}
    />
  )
}
