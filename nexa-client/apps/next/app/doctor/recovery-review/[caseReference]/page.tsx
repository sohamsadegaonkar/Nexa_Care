'use client'

import { useParams, useRouter } from 'next/navigation'
import RegistrationRecoveryReviewDetailScreen from 'app/features/recoveryReview/RegistrationRecoveryReviewDetailScreen'

export default function DoctorRecoveryReviewDetailPage() {
  const router = useRouter()
  const params = useParams()
  const rawParam = params?.caseReference
  const caseReference = Array.isArray(rawParam) ? rawParam[0] : (rawParam ?? '')

  return (
    <RegistrationRecoveryReviewDetailScreen
      caseReference={caseReference}
      onBack={() => router.push('/doctor/recovery-review')}
    />
  )
}
