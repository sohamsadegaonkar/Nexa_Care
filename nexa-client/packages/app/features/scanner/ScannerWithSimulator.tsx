'use client'

import { ScannerScreen } from './screen'
import { PolicySimulator } from '../dev/PolicySimulator'
import { useState } from 'react'
import type { ConsentAssurance } from '../../api/consent_v1'

export function ScannerWithSimulator() {
  const [currentPatient, setCurrentPatient] = useState<string | null>(null)
  const [currentPolicy, setCurrentPolicy] = useState<ConsentAssurance>('standard')

  const handlePatientResolved = (patientId: string) => {
    setCurrentPatient(patientId)
  }

  return (
    <>
      <ScannerScreen
        onPatientResolved={handlePatientResolved}
        isDev={true}
      />

      {currentPatient && (
        <PolicySimulator
          patientUuid={currentPatient}
          onPolicyChange={setCurrentPolicy}
        />
      )}
    </>
  )
}
