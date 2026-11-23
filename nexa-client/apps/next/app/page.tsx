import { Suspense } from 'react'
import { DoctorLoginScreen } from 'app/features/doctor/DoctorLoginScreen'

export default function Page() {
  const stagingPreview = process.env.NEXA_STAGING_WEB_PREVIEW === '1'
  return (
    <>
      {stagingPreview ? (
        <aside
          role="status"
          style={{
            padding: '12px 20px',
            background: '#202a3e',
            color: '#fff',
            borderBottom: '2px solid #f59e0b',
            fontFamily: 'system-ui, sans-serif',
            textAlign: 'center',
          }}
        >
          <strong>Nexa Care staging — web interface preview only.</strong>{' '}
          Synthetic OTP, backend login and clinical workflows are not connected yet.
          Do not enter real patient information.
        </aside>
      ) : null}
      <Suspense fallback={null}>
        <DoctorLoginScreen showEntryOptions />
      </Suspense>
    </>
  )
}
