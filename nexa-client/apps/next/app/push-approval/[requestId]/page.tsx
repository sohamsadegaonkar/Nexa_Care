'use client'

import { PatientApprovalScreen } from 'app/features/approval/PatientApprovalScreen'
import { useParams } from 'solito/navigation'

export default function ApprovalPage() {
  const { requestId } = useParams<{ requestId: string }>()

  return <PatientApprovalScreen requestId={requestId as string} />
}
