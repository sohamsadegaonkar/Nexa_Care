import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const ROOT = resolve(import.meta.dirname, '../../../..')

const CLINICIAN_SCREENS = [
  'packages/app/features/doctor/DoctorDashboardScreen.tsx',
  'packages/app/features/doctor/EmergencyAccessScreen.tsx',
  'packages/app/features/doctor/PatientSearchScreen.tsx',
  'packages/app/features/doctor/DocumentsWorkspaceScreen.tsx',
  'packages/app/features/doctor/PatientRecordViewerScreen.tsx',
  'packages/app/features/doctor/ProviderShell.tsx',
  'packages/app/features/doctor/components/PatientDiscoverySelector.tsx',
  'packages/app/features/adjudication/AdjudicationQueueScreen.tsx',
  'packages/app/features/adjudication/AdjudicationReviewScreen.tsx',
]

function source(path: string): string {
  return readFileSync(resolve(ROOT, path), 'utf8')
}

describe('clinician UI terminology and safety contract', () => {
  it('does not expose internal engineering jargon to doctors', () => {
    for (const file of CLINICIAN_SCREENS) {
      const code = source(file)
      // No raw pipeline routing/job/decision references in user visible copy
      expect(code).not.toMatch(/>\s*Eligible routing reference/i)
      expect(code).not.toMatch(/>\s*Create field-linked case/i)
      expect(code).not.toMatch(/>\s*SOURCE_ONLY/i)
      expect(code).not.toMatch(/>\s*QUARANTINE/i)
      // No prompt asking doctor for raw UUID
      expect(code).not.toMatch(/enter patient uuid/i)
      expect(code).not.toMatch(/canonical patient uuid/i)
      // No bearer capability leak in UI text
      expect(code).not.toMatch(/bearer token/i)
    }
  })

  it('keeps navigation clinician-oriented without adjudication jargon', () => {
    const shellCode = source('packages/app/features/doctor/ProviderShell.tsx')
    // Adjudication should NOT be a top-level nav item in the provider shell
    expect(shellCode).not.toContain("label: 'Adjudication'")
    expect(shellCode).toContain("label: 'Documents'")
  })

  it('never stores credentials or discovery tokens in client storage', () => {
    for (const file of CLINICIAN_SCREENS) {
      const code = source(file)
      expect(code).not.toMatch(/\blocalStorage\b/)
      expect(code).not.toMatch(/\bsessionStorage\b/)
      expect(code).not.toMatch(/\bindexedDB\b/)
    }
  })

  it('never leaks patient identifier or consent tokens into navigation query parameters', () => {
    for (const file of CLINICIAN_SCREENS) {
      const code = source(file)
      // URLs must not encode sensitive patient identifiers or tokens
      expect(code).not.toMatch(/[?&](?:patient_uuid|patientId|consent_token|discovery_handle)=/)
    }
  })
})
