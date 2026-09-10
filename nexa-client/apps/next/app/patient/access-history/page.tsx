'use client'

import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  ActionButton,
  InlineNotice,
  Paragraph,
  StatusBadge,
  Surface,
  Text,
  XStack,
  YStack,
} from '@my/ui'
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  Building2,
  Clock,
  Filter,
  RefreshCw,
  Search,
  ShieldAlert,
  ShieldCheck,
  UserCheck,
} from '@tamagui/lucide-icons'
import { usePatientAuthSession } from 'app/services/patientAuthSession'
import { apiClient } from 'app/utils/apiClient'

interface AccessHistoryEntry {
  audit_id: string
  accessed_by?: string
  doctor_name: string
  hospital_name: string
  purpose: string
  accessed_at: string
  data_categories: string[]
  is_break_glass: boolean
  flag: 'BREAK_GLASS_ACCESS' | 'ROUTINE_ACCESS'
}

export default function PatientAccessHistoryPage() {
  const router = useRouter()
  const session = usePatientAuthSession()
  const [history, setHistory] = useState<AccessHistoryEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [filterType, setFilterType] = useState<'ALL' | 'ROUTINE' | 'EMERGENCY'>('ALL')
  const [searchQuery, setSearchQuery] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [nextCursor, setNextCursor] = useState<string | null>(null)

  const fetchHistory = useCallback(async (mode: 'initial' | 'refresh' = 'initial') => {
    if (mode === 'refresh') setRefreshing(true)
    else setLoading(true)
    setError(null)

    try {
      const response = await apiClient.get<any>('/api/v2/patient/me/access-history')
      const payload = response?.data || response
      const items = Array.isArray(payload?.access_history) ? payload.access_history : []
      setHistory(items)
      setNextCursor(payload?.next_cursor || null)
    } catch (err: any) {
      // In unauthenticated or dev environment, provide informative feedback
      if (session.status !== 'authenticated') {
        setError('Sign in to view your live cryptographic audit ledger.')
      } else {
        setError('Unable to retrieve access history ledger. Please check network connection.')
      }
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [session.status])

  useEffect(() => {
    fetchHistory('initial')
  }, [fetchHistory])

  const filteredHistory = history.filter((item) => {
    if (filterType === 'ROUTINE' && item.is_break_glass) return false
    if (filterType === 'EMERGENCY' && !item.is_break_glass) return false
    if (!searchQuery.trim()) return true
    const q = searchQuery.toLowerCase()
    return (
      item.doctor_name?.toLowerCase().includes(q) ||
      item.hospital_name?.toLowerCase().includes(q) ||
      item.purpose?.toLowerCase().includes(q)
    )
  })

  return (
    <YStack gap="$5" maxWidth={1100} width="100%" marginHorizontal="auto">
      {/* Header with Navigation & Actions */}
      <XStack
        flexWrap="wrap"
        justifyContent="space-between"
        alignItems="center"
        gap="$3"
      >
        <YStack gap="$1.5">
          <XStack alignItems="center" gap="$2">
            <ActionButton
              chromeless
              paddingHorizontal="$2"
              onPress={() => router.push('/patient/dashboard')}
            >
              <XStack alignItems="center" gap="$1.5">
                <ArrowLeft size={16} color="$nexaSecondary" />
                <Text color="$nexaSecondary" fontSize={13} fontWeight="600">
                  Dashboard
                </Text>
              </XStack>
            </ActionButton>
          </XStack>
          <Text fontSize={26} fontWeight="900" color="$nexaText">
            Access History
          </Text>
          <Paragraph color="$nexaSecondary" fontSize={14}>
            See when healthcare professionals accessed your information. Each routine access requires patient consent; emergency access is recorded with the clinician's stated reason.
          </Paragraph>
        </YStack>

        <XStack gap="$2" alignItems="center">
          <ActionButton
            onPress={() => fetchHistory('refresh')}
            disabled={loading || refreshing}
          >
            <XStack alignItems="center" gap="$2">
              <RefreshCw size={15} color="$nexaText" />
              <Text color="$nexaText" fontSize={13} fontWeight="600">
                {refreshing ? 'Refreshing…' : 'Refresh'}
              </Text>
            </XStack>
          </ActionButton>
          <ActionButton
            intent="primary"
            onPress={() => router.push('/patient/timeline')}
          >
            <XStack alignItems="center" gap="$2">
              <Activity size={15} color="white" />
              <Text color="white" fontSize={13} fontWeight="700">
                View Health Timeline
              </Text>
            </XStack>
          </ActionButton>
        </XStack>
      </XStack>

      {/* Filter & Search Bar */}
      <Surface padding="$3.5" borderRadius={12} elevation="$1">
        <XStack flexWrap="wrap" gap="$3" alignItems="center" justifyContent="space-between">
          <XStack gap="$2" alignItems="center">
            <ActionButton
              onPress={() => setFilterType('ALL')}
              backgroundColor={filterType === 'ALL' ? '$nexaAccentSoft' : '$nexaSurface'}
              borderColor={filterType === 'ALL' ? '$nexaAccent' : '$nexaBorder'}
            >
              <Text
                fontSize={13}
                fontWeight="700"
                color={filterType === 'ALL' ? '$nexaAccent' : '$nexaText'}
              >
                All Accesses ({history.length})
              </Text>
            </ActionButton>
            <ActionButton
              onPress={() => setFilterType('ROUTINE')}
              backgroundColor={filterType === 'ROUTINE' ? '$nexaAccentSoft' : '$nexaSurface'}
              borderColor={filterType === 'ROUTINE' ? '$nexaAccent' : '$nexaBorder'}
            >
              <XStack alignItems="center" gap="$1.5">
                <ShieldCheck size={14} color={filterType === 'ROUTINE' ? '$nexaAccent' : '$nexaSecondary'} />
                <Text
                  fontSize={13}
                  fontWeight="700"
                  color={filterType === 'ROUTINE' ? '$nexaAccent' : '$nexaText'}
                >
                  Routine
                </Text>
              </XStack>
            </ActionButton>
            <ActionButton
              onPress={() => setFilterType('EMERGENCY')}
              backgroundColor={filterType === 'EMERGENCY' ? '$nexaAccentSoft' : '$nexaSurface'}
              borderColor={filterType === 'EMERGENCY' ? '$nexaAccent' : '$nexaBorder'}
            >
              <XStack alignItems="center" gap="$1.5">
                <ShieldAlert size={14} color={filterType === 'EMERGENCY' ? '$nexaAccent' : '$nexaSecondary'} />
                <Text
                  fontSize={13}
                  fontWeight="700"
                  color={filterType === 'EMERGENCY' ? '$nexaAccent' : '$nexaText'}
                >
                  Emergency Break-Glass
                </Text>
              </XStack>
            </ActionButton>
          </XStack>

          <XStack
            alignItems="center"
            gap="$2"
            paddingHorizontal="$3"
            paddingVertical="$2"
            borderRadius={8}
            backgroundColor="$nexaSurface"
            borderWidth={1}
            borderColor="$nexaBorder"
            minWidth={220}
          >
            <Search size={15} color="$nexaSecondary" />
            <input
              type="text"
              placeholder="Filter loaded records by doctor or hospital…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              style={{
                border: 'none',
                background: 'transparent',
                outline: 'none',
                fontSize: '13px',
                color: 'inherit',
                width: '100%',
              }}
            />
          </XStack>
        </XStack>
      </Surface>

      {/* Error or Notice */}
      {error && (
        <InlineNotice
          tone="neutral"
          title="Audit Ledger Notice"
          description={error}
        />
      )}

      {/* Access History List */}
      <YStack gap="$3">
        {filteredHistory.length > 0 ? (
          filteredHistory.map((item, idx) => (
            <Surface
              key={item.audit_id || idx}
              padding="$4"
              borderRadius={14}
              elevation="$1"
              borderWidth={1}
              borderColor={item.is_break_glass ? '$red8' : '$nexaBorder'}
            >
              <YStack gap="$3">
                {/* Top Row: Provider Identity + Status Flag */}
                <XStack
                  justifyContent="space-between"
                  alignItems="flex-start"
                  flexWrap="wrap"
                  gap="$2"
                >
                  <XStack gap="$3" alignItems="center">
                    <Surface
                      padding="$2.5"
                      borderRadius={10}
                      backgroundColor={item.is_break_glass ? '$red3' : '$nexaMuted'}
                    >
                      {item.is_break_glass ? (
                        <ShieldAlert size={22} color="$red11" />
                      ) : (
                        <UserCheck size={22} color="$nexaAccent" />
                      )}
                    </Surface>
                    <YStack gap="$0.5">
                      <XStack alignItems="center" gap="$2">
                        <Text fontSize={16} fontWeight="800" color="$nexaText">
                          {item.doctor_name || 'Attending Physician'}
                        </Text>
                        <StatusBadge tone={item.is_break_glass ? 'danger' : 'success'}>
                          {item.is_break_glass ? 'Break-Glass Emergency' : 'Signed Consent'}
                        </StatusBadge>
                      </XStack>
                      <XStack alignItems="center" gap="$1.5">
                        <Building2 size={13} color="$nexaSecondary" />
                        <Text fontSize={13} color="$nexaSecondary" fontWeight="600">
                          {item.hospital_name || 'Healthcare Facility'}
                        </Text>
                      </XStack>
                    </YStack>
                  </XStack>

                  <XStack alignItems="center" gap="$1.5">
                    <Clock size={14} color="$nexaSecondary" />
                    <Text fontSize={12} color="$nexaSecondary" fontWeight="600">
                      {item.accessed_at
                        ? new Date(item.accessed_at).toLocaleString('en-IN', {
                            dateStyle: 'medium',
                            timeStyle: 'short',
                          })
                        : 'Recent'}
                    </Text>
                  </XStack>
                </XStack>

                {/* Purpose and Categories */}
                <Surface
                  padding="$3"
                  borderRadius={8}
                  backgroundColor="$nexaSurface"
                >
                  <YStack gap="$2">
                    <XStack alignItems="center" gap="$2">
                      <Text fontSize={12} fontWeight="700" color="$nexaSecondary">
                        STATED PURPOSE:
                      </Text>
                      <Text fontSize={13} fontWeight="600" color="$nexaText">
                        {item.purpose || 'Clinical care and diagnosis review'}
                      </Text>
                    </XStack>

                    {Array.isArray(item.data_categories) && item.data_categories.length > 0 && (
                      <XStack flexWrap="wrap" gap="$1.5" alignItems="center">
                        <Text fontSize={12} fontWeight="700" color="$nexaSecondary">
                          CATEGORIES VIEWED:
                        </Text>
                        {item.data_categories.map((cat, cIdx) => (
                          <Surface
                            key={cIdx}
                            paddingHorizontal="$2"
                            paddingVertical="$1"
                            borderRadius={6}
                            backgroundColor="$nexaMuted"
                          >
                            <Text fontSize={11} fontWeight="700" color="$nexaText">
                              {cat}
                            </Text>
                          </Surface>
                        ))}
                      </XStack>
                    )}
                  </YStack>
                </Surface>

                {/* Audit Integrity Footer */}
                <XStack justifyContent="space-between" alignItems="center">
                  <Text fontSize={11} color="$nexaSecondary">
                    Audit ID: {item.audit_id || '0000-0000-0000-0000'}
                  </Text>
                  <XStack alignItems="center" gap="$1">
                    <ShieldCheck size={13} color="$nexaAccent" />
                    <Text fontSize={11} color="$nexaAccent" fontWeight="700">
                      Audited access
                    </Text>
                  </XStack>
                </XStack>
              </YStack>
            </Surface>
          ))
        ) : (
          <Surface
            padding="$6"
            borderRadius={16}
            alignItems="center"
            justifyContent="center"
            elevation="$1"
          >
            <YStack alignItems="center" gap="$3" maxWidth={420} paddingVertical="$4">
              <ShieldCheck size={48} color="$nexaAccent" />
              <Text fontSize={18} fontWeight="800" color="$nexaText" textAlign="center">
                No provider has accessed your records yet.
              </Text>
              <Paragraph color="$nexaSecondary" fontSize={14} textAlign="center">
                Whenever a physician, emergency unit, or laboratory accesses your health summary, a cryptographic proof will immediately be recorded here.
              </Paragraph>
              <ActionButton
                intent="primary"
                onPress={() => router.push('/patient/timeline')}
              >
                Explore Health Timeline
              </ActionButton>
            </YStack>
          </Surface>
        )}
      </YStack>
    </YStack>
  )
}
