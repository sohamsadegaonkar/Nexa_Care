'use client'

import { useState } from 'react'
import { HomeScreen } from '../home/screen'
import { ScannerScreen } from '../scanner/screen'
import { DashboardScreen } from '../dashboard/DashboardScreen'
import { BreakGlassScreen } from '../emergency/BreakGlassScreen'
import { ConsentHistoryScreen } from '../consent/ConsentHistoryScreen'
import { LoginScreen } from '../user/LoginScreen'
import { PolicyScreen } from '../assurance/PolicyScreen'
import { updatePatientPolicy } from '../../api/policy'
import { MergeAdminScreen } from '../admin/MergeAdminScreen'
import { PatientTransparencyScreen } from '../patient/PatientTransparencyScreen'

export type UserRole = 'receptionist' | 'clinician' | 'admin'

interface RoleNavigatorProps {
  role: UserRole
  patientUuid?: string
}

export function RoleNavigator({ role, patientUuid: initialPatientUuid, mfaToken }: RoleNavigatorProps) {
  const [currentScreen, setCurrentScreen] = useState('home')
  const [activePatientUuid, setActivePatientUuid] = useState<string | undefined>(initialPatientUuid)

  const renderScreen = () => {
    switch (currentScreen) {
      case 'home':
        return <HomeScreen onNavigate={setCurrentScreen} />
      case 'scanner':
        return (
          <ScannerScreen 
            onPatientResolved={(pid) => setActivePatientUuid(pid)}
            onOpenPolicy={(pid) => {
              setActivePatientUuid(pid)
              setCurrentScreen('policy')
            }}
          />
        )
      case 'dashboard':
        return role !== 'receptionist' ? <DashboardScreen /> : <HomeScreen />
      case 'break-glass':
        return role !== 'receptionist' ? <BreakGlassScreen /> : <HomeScreen />
      case 'consent-history':
        return <ConsentHistoryScreen />
      case 'policy':
        return (
          <PolicyScreen 
            currentPolicy="standard"
            patientUuid={activePatientUuid}
            onPolicyChange={async (newPolicy) => {
              if (!activePatientUuid) {
                alert("No patient selected")
                return
              }
              try {
                await updatePatientPolicy(activePatientUuid, newPolicy)
                alert(`Policy saved: ${newPolicy}`)
              } catch (e) {
                alert("Failed to save policy")
              }
            }} 
          />
        )
      case 'merge':
        return role === 'admin' ? <MergeAdminScreen mfaToken={mfaToken} /> : <HomeScreen />
      case 'transparency':
        return <PatientTransparencyScreen patientUuid={activePatientUuid || "demo-patient"} />
      default:
        return <HomeScreen onNavigate={setCurrentScreen} />
    }
  }

  return renderScreen()
}
