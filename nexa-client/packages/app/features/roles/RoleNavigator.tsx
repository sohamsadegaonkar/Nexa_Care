'use client'

import { useState } from 'react'
import { Button, Text, XStack, YStack } from '@my/ui'
import { HomeScreen } from '../home/screen'
import { ScannerScreen } from '../scanner/screen'
import { DashboardScreen } from '../dashboard/DashboardScreen'
import { BreakGlassScreen } from '../emergency/BreakGlassScreen'
import { ConsentHistoryScreen } from '../consent/ConsentHistoryScreen'
import { PolicyScreen } from '../assurance/PolicyScreen'
import { updatePatientPolicy } from '../../api/policy'
import { MergeAdminScreen } from '../admin/MergeAdminScreen'
import { PatientTransparencyScreen } from '../patient/PatientTransparencyScreen'

export type UserRole = 'receptionist' | 'clinician' | 'admin'

interface RoleNavigatorProps {
  role: UserRole
  patientUuid?: string
  onLogout: () => void
  onRoleRefresh: () => Promise<void>
}

function PatientRequired({ onBack }: { onBack: () => void }) {
  return (
    <YStack
      p="$6"
      gap="$4"
      items="center"
    >
      <Text>No authorized patient is selected.</Text>
      <Button onPress={onBack}>Return to patient selection</Button>
    </YStack>
  )
}

export function RoleNavigator({
  role,
  patientUuid: initialPatientUuid,
  onLogout,
  onRoleRefresh,
}: RoleNavigatorProps) {
  const [currentScreen, setCurrentScreen] = useState('home')
  const [activePatientUuid, setActivePatientUuid] = useState<string | undefined>(initialPatientUuid)

  const goHome = () => setCurrentScreen('home')
  const renderScreen = () => {
    switch (currentScreen) {
      case 'home':
        return <HomeScreen onNavigate={setCurrentScreen} />
      case 'scanner':
        return <ScannerScreen onPatientResolved={setActivePatientUuid} />
      case 'dashboard':
        return role !== 'receptionist' ? (
          <DashboardScreen />
        ) : (
          <HomeScreen onNavigate={setCurrentScreen} />
        )
      case 'break-glass':
        return role === 'clinician' ? (
          <BreakGlassScreen />
        ) : (
          <HomeScreen onNavigate={setCurrentScreen} />
        )
      case 'consent-history':
        return <ConsentHistoryScreen />
      case 'policy':
        if (!activePatientUuid || (role !== 'clinician' && role !== 'admin')) {
          return <PatientRequired onBack={goHome} />
        }
        return (
          <PolicyScreen
            currentPolicy="standard"
            patientUuid={activePatientUuid}
            onPolicyChange={async (newPolicy) => {
              await updatePatientPolicy(activePatientUuid, newPolicy)
            }}
          />
        )
      case 'merge':
        return role === 'admin' ? (
          <MergeAdminScreen />
        ) : (
          <HomeScreen onNavigate={setCurrentScreen} />
        )
      case 'transparency':
        return activePatientUuid ? (
          <PatientTransparencyScreen patientUuid={activePatientUuid} />
        ) : (
          <PatientRequired onBack={goHome} />
        )
      default:
        return <HomeScreen onNavigate={setCurrentScreen} />
    }
  }

  return (
    <YStack flex={1}>
      <XStack
        gap="$2"
        p="$2"
        justify="flex-end"
      >
        <Button
          size="$2"
          onPress={() => void onRoleRefresh()}
        >
          Refresh access
        </Button>
        <Button
          size="$2"
          onPress={() => {
            setActivePatientUuid(undefined)
            onLogout()
          }}
        >
          Sign out
        </Button>
      </XStack>
      {renderScreen()}
    </YStack>
  )
}
