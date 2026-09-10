'use client'

import type { ReactNode } from 'react'
import { ActionButton, Brand, Paragraph, StatusBadge, SwitchThemeButton, Text, XStack, YStack } from '@my/ui'
import {
  LayoutDashboard,
  Users,
  FileText,
  ClipboardCheck,
  ShieldAlert,
  Building2,
  UserCheck,
} from '@tamagui/lucide-icons'
import { usePathname, useRouter } from 'next/navigation'
import { useProviderAuth } from './ProviderAuthContext'

const navigation = [
  {
    label: 'Workspace',
    path: '/doctor/dashboard',
    icon: LayoutDashboard,
    matches: (path: string) => path === '/doctor/dashboard',
  },
  {
    label: 'Patients',
    path: '/doctor/patient-search',
    icon: Users,
    matches: (path: string) => /\/doctor\/(patient|request-consent|waiting)/.test(path),
  },
  {
    label: 'Documents',
    path: '/doctor/patient-search?intent=document_upload',
    icon: FileText,
    matches: (path: string) =>
      path.startsWith('/doctor/pipeline/') && !path.includes('adjudication'),
  },
  {
    label: 'Adjudication',
    path: '/doctor/pipeline/adjudication',
    icon: ClipboardCheck,
    matches: (path: string) => path.includes('/adjudication'),
  },
]

// Presentation only. Each workflow retains its existing authority and route guards.
export function ProviderShell({ children }: { children: ReactNode }) {
  const pathname = usePathname()
  const router = useRouter()
  const { hydrated, isAuthenticated, displayName, hospitalName, role, logout } = useProviderAuth()
  if (pathname === '/doctor/login' || !hydrated || !isAuthenticated) return children

  return (
    <YStack
      minHeight="100vh"
      backgroundColor="$nexaCanvas"
    >
      <a
        href="#provider-content"
        className="nexa-skip-link"
      >
        Skip to workspace
      </a>
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
          gap="$4"
          flexWrap="wrap"
        >
          <Brand compact />
          <XStack
            alignItems="center"
            gap="$2"
            paddingHorizontal="$3"
            paddingVertical="$1.5"
            borderRadius={8}
            backgroundColor="$nexaMuted"
          >
            <Building2
              size={16}
              color="$nexaSecondary"
            />
            <Text
              color="$nexaText"
              fontSize={13}
              fontWeight="600"
            >
              {hospitalName || 'Facility'}
            </Text>
          </XStack>
        </XStack>
        <XStack
          gap="$3"
          alignItems="center"
          flexWrap="wrap"
        >
          <XStack
            alignItems="center"
            gap="$2"
            paddingHorizontal="$3"
            paddingVertical="$1.5"
            borderRadius={8}
            borderWidth={1}
            borderColor="$nexaBorder"
            backgroundColor="$nexaSurface"
          >
            <UserCheck
              size={15}
              color="$nexaAccent"
            />
            <Text
              color="$nexaText"
              fontSize={13}
              fontWeight="700"
            >
              {displayName || 'Provider'}
            </Text>
            {role && (
              <StatusBadge tone="info">
                {role}
              </StatusBadge>
            )}
          </XStack>
          <SwitchThemeButton />
          <ActionButton
            onPress={() => {
              logout()
              router.push('/doctor/login')
            }}
          >
            Sign Out
          </ActionButton>
        </XStack>
      </XStack>
      <XStack
        flexGrow={1}
        flexShrink={0}
        flexDirection="column"
        $lg={{ flexDirection: 'row' }}
      >
        <YStack
          render="nav"
          aria-label="Provider navigation"
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
            <ActionButton
              intent="danger"
              justifyContent="flex-start"
              aria-current={pathname === '/doctor/emergency-access' ? 'page' : undefined}
              onPress={() => router.push('/doctor/emergency-access')}
            >
              <XStack
                alignItems="center"
                gap="$2.5"
                width="100%"
              >
                <ShieldAlert
                  size={18}
                  color="$nexaDanger"
                />
                <Text
                  color="$nexaDanger"
                  fontWeight="700"
                  fontSize={14}
                >
                  Emergency access
                </Text>
              </XStack>
            </ActionButton>
          </XStack>
        </YStack>
        <YStack
          render="main"
          id="provider-content"
          tabIndex={-1}
          flexGrow={1}
          flexShrink={1}
          minWidth={0}
        >
          {children}
        </YStack>
      </XStack>
    </YStack>
  )
}

