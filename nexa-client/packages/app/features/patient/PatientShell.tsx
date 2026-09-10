'use client'

import type { ReactNode } from 'react'
import { ActionButton, Brand, StatusBadge, SwitchThemeButton, Text, XStack, YStack } from '@my/ui'
import {
  LayoutDashboard,
  Clock,
  Activity,
  User,
  ShieldCheck,
  LogOut,
} from '@tamagui/lucide-icons'
import { usePathname, useRouter } from 'next/navigation'
import { clearPatientAuthSession, usePatientAuthSession } from '../../services/patientAuthSession'

const navigation = [
  {
    label: 'Dashboard',
    path: '/patient/dashboard',
    icon: LayoutDashboard,
    matches: (path: string) => path === '/patient/dashboard' || path === '/patient',
  },
  {
    label: 'Access History',
    path: '/patient/access-history',
    icon: Clock,
    matches: (path: string) => path === '/patient/access-history',
  },
  {
    label: 'Health Timeline',
    path: '/patient/timeline',
    icon: Activity,
    matches: (path: string) => path === '/patient/timeline',
  },
  {
    label: 'My Profile',
    path: '/patient/profile',
    icon: User,
    matches: (path: string) => path === '/patient/profile',
  },
]

export function PatientShell({ children }: { children: ReactNode }) {
  const pathname = usePathname()
  const router = useRouter()
  const session = usePatientAuthSession()

  const isLoginPage = pathname === '/patient/login'
  if (isLoginPage) return <>{children}</>

  const handleSignOut = async () => {
    try {
      await clearPatientAuthSession('logout')
    } catch {
      // safe fallback
    }
    router.push('/patient/login')
  }

  return (
    <YStack
      minHeight="100vh"
      backgroundColor="$nexaCanvas"
    >
      {/* Top Header */}
      <XStack
        render="header"
        flexWrap="wrap"
        alignItems="center"
        justifyContent="space-between"
        paddingHorizontal="$4"
        paddingVertical="$3"
        gap="$3"
        backgroundColor="$nexaSurface"
        borderBottomWidth={1}
        borderBottomColor="$nexaBorder"
        elevation="$1"
      >
        <XStack
          alignItems="center"
          gap="$3"
          flexWrap="wrap"
        >
          <Brand compact />
          <StatusBadge tone="success">
            Patient Portal
          </StatusBadge>
        </XStack>

        <XStack
          gap="$2.5"
          alignItems="center"
          flexWrap="wrap"
        >
          {session.status === 'authenticated' && (
            <XStack
              alignItems="center"
              gap="$1.5"
              paddingHorizontal="$3"
              paddingVertical="$1.5"
              borderRadius={8}
              backgroundColor="$nexaMuted"
            >
              <User
                size={16}
                color="$nexaAccent"
              />
              <Text
                color="$nexaText"
                fontSize={12}
                fontWeight="700"
              >
                Patient session active
              </Text>
            </XStack>
          )}
          <SwitchThemeButton />
          <ActionButton
            onPress={handleSignOut}
          >
            <XStack alignItems="center" gap="$1.5">
              <LogOut size={15} color="$nexaText" />
              <Text color="$nexaText" fontSize={13} fontWeight="600">Sign Out</Text>
            </XStack>
          </ActionButton>
        </XStack>
      </XStack>

      {/* Main Layout with Navigation */}
      <XStack
        flexGrow={1}
        flexShrink={0}
        flexDirection="column"
        $lg={{ flexDirection: 'row' }}
      >
        {/* Navigation Bar / Sidebar */}
        <YStack
          render="nav"
          aria-label="Patient navigation"
          padding="$3"
          gap="$2"
          backgroundColor="$nexaSurface"
          borderBottomWidth={1}
          borderColor="$nexaBorder"
          $lg={{ width: 236, borderBottomWidth: 0, borderRightWidth: 1, padding: '$4' }}
        >
          <XStack
            flexWrap="wrap"
            gap="$2"
            $lg={{ flexDirection: 'column' }}
          >
            {navigation.map((item) => {
              const Icon = item.icon
              const isCurrent = item.matches(pathname)
              return (
                <ActionButton
                  key={item.label}
                  justifyContent="flex-start"
                  aria-current={isCurrent ? 'page' : undefined}
                  backgroundColor={isCurrent ? '$nexaAccentSoft' : '$nexaSurface'}
                  borderColor={isCurrent ? '$nexaAccent' : 'transparent'}
                  hoverStyle={{
                    backgroundColor: isCurrent ? '$nexaAccentSoft' : '$nexaMuted',
                    borderColor: '$nexaAccent',
                  }}
                  onPress={() => router.push(item.path)}
                >
                  <XStack
                    alignItems="center"
                    gap="$2.5"
                    width="100%"
                  >
                    <Icon
                      size={18}
                      color={isCurrent ? '$nexaAccent' : '$nexaSecondary'}
                    />
                    <Text
                      color={isCurrent ? '$nexaAccent' : '$nexaText'}
                      fontWeight={isCurrent ? '700' : '600'}
                      fontSize={14}
                    >
                      {item.label}
                    </Text>
                  </XStack>
                </ActionButton>
              )
            })}
          </XStack>
        </YStack>

        {/* Content Area */}
        <YStack
          render="main"
          flexGrow={1}
          flexShrink={1}
          minWidth={0}
          padding="$4"
          $md={{ padding: '$6' }}
        >
          {children}
        </YStack>
      </XStack>
    </YStack>
  )
}
