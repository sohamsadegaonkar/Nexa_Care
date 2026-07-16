/**
 * Doctor dashboard screen — entry point after login.
 *
 * Shows the provider's name, hospital, and role from the session context.
 * Quick actions: search patient, scan NFC, emergency access.
 * No hardcoded provider_id.
 *
 * Guards: If no active session, redirects to /doctor/login.
 *
 * Route: /doctor/dashboard
 */

'use client'

import { Card, Text, YStack, Button, XStack, Separator, Spinner, Paragraph } from '@my/ui'
import { useRouter } from 'next/navigation'
import { useProviderAuth } from './ProviderAuthContext'
import { useState, useEffect } from 'react'

export function DoctorDashboardScreen() {
  const router = useRouter()
  const { hydrated, isAuthenticated, displayName, hospitalName, providerId, role, logout } = useProviderAuth()
  const [pendingCount, setPendingCount] = useState<number>(0)
  const [pendingLoading, setPendingLoading] = useState(true)
  const [pendingError, setPendingError] = useState<string | null>(null)

  // ── Session guard — redirect to login if not authenticated ────────────
  useEffect(() => {
    if (hydrated && !isAuthenticated) {
      router.replace('/doctor/login')
    }
  }, [hydrated, isAuthenticated, router])

  // ── Fetch pending consent request count ───────────────────────────────
  useEffect(() => {
    if (!isAuthenticated) return

    let cancelled = false
    const fetchPending = async () => {
      setPendingLoading(true)
      setPendingError(null)
      try {
        // ALPHA: No dedicated pending-count endpoint yet.
        setPendingCount(0)
      } catch {
        if (!cancelled) {
          setPendingError('Could not load pending requests.')
        }
      } finally {
        if (!cancelled) {
          setPendingLoading(false)
        }
      }
    }
    fetchPending()
    return () => { cancelled = true }
  }, [isAuthenticated])

  const handleLogout = () => {
    logout()
    router.push('/doctor/login')
  }

  // ── Unauthenticated — render nothing while redirecting ────────────────

  if (!hydrated || !isAuthenticated) {
    return (
      <YStack flex={1} bg="$background" justifyContent="center" alignItems="center">
        <Spinner size="large" color="$blue10" />
      </YStack>
    )
  }

  return (
    <YStack flex={1} bg="$background" padding="$5" gap="$5" maxWidth={900} marginHorizontal="auto">
      {/* Header */}
      <XStack alignItems="center" justifyContent="space-between">
        <YStack>
          <Text fontSize={28} fontWeight="900" color="$color12">Dashboard</Text>
          <Paragraph color="$color11" fontSize={15}>
            {displayName || 'Provider'} · {hospitalName || 'Hospital'}
          </Paragraph>
          <Paragraph color="$color11" fontSize={13}>
            Role: {role || 'clinician'}
          </Paragraph>
        </YStack>
        <Button size="$3" chromeless onPress={handleLogout}>Sign Out</Button>
      </XStack>

      <Separator />

      {/* Provider info card */}
      <Card padding="$4" backgroundColor="$backgroundHover" borderRadius="$4" gap="$2">
        <Text color="$color11" fontSize={12} textTransform="uppercase" letterSpacing={1}>
          Provider Identity
        </Text>
        <Text color="$color12" fontSize={18} fontWeight="600" fontFamily="$mono">
          {providerId || '—'}
        </Text>
      </Card>

      {/* Quick actions */}
      <YStack gap="$3" maxWidth={600}>
        <Button
          theme="blue"
          size="$4"
          onPress={() => router.push('/doctor/patient-search')}
        >
          🔍 Search Patient
        </Button>

        <Button
          theme="blue"
          size="$4"
          onPress={() => router.push('/doctor/patient-search?mode=nfc')}
        >
          📱 Scan NFC Card
        </Button>

        <Button
          theme="orange"
          size="$4"
          onPress={() => router.push('/doctor/emergency-access')}
        >
          🚨 Emergency Access
        </Button>
      </YStack>

      <Separator />

      {/* Pending consent requests */}
      <YStack gap="$2">
        <Paragraph color="$color11" fontSize={15} fontWeight="600">
          Pending Consent Requests
        </Paragraph>
        {pendingLoading ? (
          <Spinner size="small" color="$blue10" />
        ) : pendingError ? (
          <YStack backgroundColor="$red2" borderRadius="$3" padding="$3" gap="$2">
            <Text color="$red10" fontSize={14}>{pendingError}</Text>
            <Button
              size="$2"
              chromeless
              onPress={() => {
                setPendingLoading(true)
                setPendingError(null)
                setPendingCount(0)
              }}
            >
              Retry
            </Button>
          </YStack>
        ) : pendingCount > 0 ? (
          <YStack backgroundColor="$orange4" borderRadius="$3" padding="$3">
            <Text color="$orange10" fontSize={16} fontWeight="600">
              {pendingCount} request{pendingCount !== 1 ? 's' : ''} awaiting patient approval
            </Text>
          </YStack>
        ) : (
          <YStack backgroundColor="$green4" borderRadius="$3" padding="$3">
            <Text color="$green10" fontSize={16}>No pending requests</Text>
          </YStack>
        )}
      </YStack>
    </YStack>
  )
}
