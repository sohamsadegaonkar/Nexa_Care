'use client'

import { Button, Card, Text, YStack } from '@my/ui'
import { UserRole } from './RoleNavigator'

interface RoleSelectorProps {
  onRoleSelected: (role: UserRole) => void
}

const ROLES: { role: UserRole; label: string; desc: string }[] = [
  { role: 'receptionist', label: 'Receptionist', desc: 'NFC scanning & patient registration' },
  { role: 'clinician', label: 'Clinician', desc: 'Patient records, emergency access, dashboard' },
  { role: 'admin', label: 'Administrator', desc: 'Merge patients, audit, policy management' },
]

export function RoleSelector({ onRoleSelected }: RoleSelectorProps) {
  return (
    <YStack flex={1} bg="$background" p="$6" gap="$5" justify="center">
      <Text fontSize={26} fontWeight="900" color="$color12" text="center">
        Select Your Role
      </Text>
      <Text color="$color11" text="center">This determines the screens you can access</Text>

      <YStack gap="$3" pt="$4">
        {ROLES.map(({ role, label, desc }) => (
          <Card
            key={role}
            p="$4"
            bg="$color2"
            borderWidth={1}
            borderColor="$borderColor"
            pressStyle={{ scale: 0.985 }}
            onPress={() => onRoleSelected(role)}
          >
            <YStack gap="$1">
              <Text fontSize={18} fontWeight="800" color="$color12">{label}</Text>
              <Text color="$color11">{desc}</Text>
            </YStack>
          </Card>
        ))}
      </YStack>
    </YStack>
  )
}
