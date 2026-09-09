'use client'

import type { ReactNode } from 'react'
import { ActionButton, Brand, Paragraph, SwitchThemeButton, XStack, YStack } from '@my/ui'
import { usePathname, useRouter } from 'next/navigation'
import { useProviderAuth } from './ProviderAuthContext'

const navigation = [
  {
    label: 'Workspace',
    path: '/doctor/dashboard',
    matches: (path: string) => path === '/doctor/dashboard',
  },
  {
    label: 'Patients',
    path: '/doctor/patient-search',
    matches: (path: string) => /\/doctor\/(patient|request-consent|waiting)/.test(path),
  },
  {
    label: 'Documents',
    path: '/doctor/patient-search?intent=document_upload',
    matches: (path: string) =>
      path.startsWith('/doctor/pipeline/') && !path.includes('adjudication'),
  },
  {
    label: 'Adjudication',
    path: '/doctor/pipeline/adjudication',
    matches: (path: string) => path.includes('/adjudication'),
  },
]

// Presentation only. Each workflow retains its existing authority and route guards.
export function ProviderShell({ children }: { children: ReactNode }) {
  const pathname = usePathname()
  const router = useRouter()
  const { hydrated, isAuthenticated, hospitalName, logout } = useProviderAuth()
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
        padding="$4"
        gap="$3"
        backgroundColor="$nexaSurface"
        borderBottomWidth={1}
        borderBottomColor="$nexaBorder"
      >
        <Brand compact />
        <Paragraph
          color="$nexaSecondary"
          flex={1}
          minWidth={160}
        >
          {hospitalName || 'Facility name unavailable'}
        </Paragraph>
        <XStack
          gap="$2"
          alignItems="center"
        >
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
          $lg={{ width: 224, borderBottomWidth: 0, borderRightWidth: 1, padding: '$4' }}
        >
          <XStack
            flexWrap="wrap"
            gap="$2"
            $lg={{ flexDirection: 'column' }}
          >
            {navigation.map((item) => (
              <ActionButton
                key={item.label}
                justifyContent="flex-start"
                aria-current={item.matches(pathname) ? 'page' : undefined}
                backgroundColor={item.matches(pathname) ? '$nexaAccentSoft' : '$nexaSurface'}
                borderColor={item.matches(pathname) ? '$nexaAccent' : 'transparent'}
                onPress={() => router.push(item.path)}
              >
                {item.label}
              </ActionButton>
            ))}
            <ActionButton
              intent="danger"
              justifyContent="flex-start"
              aria-current={pathname === '/doctor/emergency-access' ? 'page' : undefined}
              onPress={() => router.push('/doctor/emergency-access')}
            >
              Emergency access
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
