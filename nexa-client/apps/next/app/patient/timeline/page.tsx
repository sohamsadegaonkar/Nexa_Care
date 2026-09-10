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
  Calendar,
  CheckCircle,
  Clock,
  FileText,
  Heart,
  Pill,
  RefreshCw,
  Search,
  ShieldCheck,
  Stethoscope,
  TestTube,
} from '@tamagui/lucide-icons'
import { usePatientAuthSession } from 'app/services/patientAuthSession'
import { apiClient } from 'app/utils/apiClient'

interface TimelineEntry {
  event_id: string
  event_type: string
  title: string
  summary: string
  description?: string
  event_date?: string
  occurred_at?: string
  source: 'manual' | 'ai_extracted' | string
  source_display?: string
  confidence?: number | null
  risk_level?: string | null
  review_status?: string | null
  badges?: string[]
}

const CATEGORY_MAP: Record<string, { label: string; icon: any; color: string }> = {
  VITALS: { label: 'Vitals', icon: Heart, color: '$red10' },
  MEDICATION: { label: 'Medication', icon: Pill, color: '$blue10' },
  LAB_RESULT: { label: 'Lab Result', icon: TestTube, color: '$purple10' },
  ALLERGY: { label: 'Allergy Alert', icon: AlertTriangle, color: '$orange10' },
  ENCOUNTER: { label: 'Clinical Encounter', icon: Stethoscope, color: '$green10' },
  DIAGNOSIS: { label: 'Diagnosis', icon: FileText, color: '$indigo10' },
  DOCUMENT: { label: 'Medical Document', icon: FileText, color: '$cyan10' },
}

export default function PatientTimelinePage() {
  const router = useRouter()
  const session = usePatientAuthSession()
  const [events, setEvents] = useState<TimelineEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [activeCategory, setActiveCategory] = useState<string>('ALL')
  const [searchQuery, setSearchQuery] = useState('')
  const [error, setError] = useState<string | null>(null)

  const fetchTimeline = useCallback(async (mode: 'initial' | 'refresh' = 'initial') => {
    if (mode === 'refresh') setRefreshing(true)
    else setLoading(true)
    setError(null)

    try {
      const response = await apiClient.get<any>('/api/v2/patient/me/timeline')
      const payload = response?.data || response
      const items = Array.isArray(payload?.events) ? payload.events : []
      setEvents(items)
    } catch {
      if (session.status !== 'authenticated') {
        setError('Sign in to view your real-time medical timeline.')
      } else {
        setError('Unable to load clinical events. Please try again.')
      }
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [session.status])

  useEffect(() => {
    fetchTimeline('initial')
  }, [fetchTimeline])

  const filteredEvents = events.filter((item) => {
    if (activeCategory !== 'ALL' && item.event_type !== activeCategory) return false
    if (!searchQuery.trim()) return true
    const q = searchQuery.toLowerCase()
    return (
      item.title?.toLowerCase().includes(q) ||
      item.summary?.toLowerCase().includes(q) ||
      item.description?.toLowerCase().includes(q)
    )
  })

  return (
    <YStack gap="$5" maxWidth={1100} width="100%" marginHorizontal="auto">
      {/* Header */}
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
            Health Timeline
          </Text>
          <Paragraph color="$nexaSecondary" fontSize={14}>
            Unified, longitudinal view of encounters, prescriptions, vitals, and adjudicated laboratory findings.
          </Paragraph>
        </YStack>

        <XStack gap="$2" alignItems="center">
          <ActionButton
            onPress={() => fetchTimeline('refresh')}
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
            onPress={() => router.push('/patient/access-history')}
          >
            <XStack alignItems="center" gap="$2">
              <Clock size={15} color="$nexaText" />
              <Text color="$nexaText" fontSize={13} fontWeight="600">
                Access Ledger
              </Text>
            </XStack>
          </ActionButton>
        </XStack>
      </XStack>

      {/* Filter and Search Bar */}
      <Surface padding="$3.5" borderRadius={12} elevation="$1">
        <XStack flexWrap="wrap" gap="$3" alignItems="center" justifyContent="space-between">
          <XStack flexWrap="wrap" gap="$2" alignItems="center">
            {['ALL', 'VITALS', 'MEDICATION', 'LAB_RESULT', 'ENCOUNTER'].map((cat) => {
              const isCurrent = activeCategory === cat
              const label = cat === 'ALL' ? 'All Events' : CATEGORY_MAP[cat]?.label || cat
              return (
                <ActionButton
                  key={cat}
                  onPress={() => setActiveCategory(cat)}
                  backgroundColor={isCurrent ? '$nexaAccentSoft' : '$nexaSurface'}
                  borderColor={isCurrent ? '$nexaAccent' : '$nexaBorder'}
                >
                  <Text
                    fontSize={13}
                    fontWeight="700"
                    color={isCurrent ? '$nexaAccent' : '$nexaText'}
                  >
                    {label}
                  </Text>
                </ActionButton>
              )
            })}
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
              placeholder="Filter timeline records…"
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

      {error && (
        <InlineNotice
          tone="neutral"
          title="Timeline Notice"
          description={error}
        />
      )}

      {/* Timeline Event Feed */}
      <YStack gap="$3">
        {filteredEvents.length > 0 ? (
          filteredEvents.map((item, idx) => {
            const cat = CATEGORY_MAP[item.event_type] || {
              label: item.event_type,
              icon: Activity,
              color: '$nexaAccent',
            }
            const Icon = cat.icon
            const isAi = item.source === 'ai_extracted'
            const isAbnormal = item.risk_level === 'HIGH_RISK' || item.risk_level === 'CRITICAL_RISK'

            return (
              <Surface
                key={item.event_id || idx}
                padding="$4"
                borderRadius={14}
                elevation="$1"
                borderWidth={1}
                borderColor={isAbnormal ? '$orange8' : '$nexaBorder'}
              >
                <YStack gap="$3">
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
                        backgroundColor="$nexaMuted"
                      >
                        <Icon size={20} color={cat.color} />
                      </Surface>
                      <YStack gap="$0.5">
                        <XStack alignItems="center" gap="$2">
                          <Text fontSize={16} fontWeight="800" color="$nexaText">
                            {item.title || cat.label}
                          </Text>
                          <StatusBadge tone="neutral">{cat.label}</StatusBadge>
                        </XStack>
                        <Text fontSize={14} color="$nexaText" fontWeight="600">
                          {item.summary}
                        </Text>
                      </YStack>
                    </XStack>

                    <XStack alignItems="center" gap="$1.5">
                      <Calendar size={14} color="$nexaSecondary" />
                      <Text fontSize={12} color="$nexaSecondary" fontWeight="600">
                        {item.occurred_at || item.event_date
                          ? new Date(item.occurred_at || item.event_date!).toLocaleDateString('en-IN', {
                              dateStyle: 'medium',
                            })
                          : 'Recent'}
                      </Text>
                    </XStack>
                  </XStack>

                  {item.description && (
                    <Surface
                      padding="$3"
                      borderRadius={8}
                      backgroundColor="$nexaSurface"
                    >
                      <Paragraph color="$nexaSecondary" fontSize={13}>
                        {item.description}
                      </Paragraph>
                    </Surface>
                  )}

                  {/* Provenance & Review Badge */}
                  <XStack justifyContent="space-between" alignItems="center" flexWrap="wrap" gap="$2">
                    <XStack alignItems="center" gap="$2">
                      {item.review_status && item.review_status !== 'N/A' ? (
                        <StatusBadge tone="accent">
                          Clinician reviewed
                        </StatusBadge>
                      ) : isAi ? (
                        <StatusBadge tone="neutral">
                          Document extracted
                        </StatusBadge>
                      ) : (
                        <StatusBadge tone="success">
                          Clinician verified
                        </StatusBadge>
                      )}
                      {item.confidence && (
                        <Text fontSize={11} color="$nexaSecondary">
                          Confidence: {Math.round(item.confidence * 100)}%
                        </Text>
                      )}
                    </XStack>

                    <Text fontSize={11} color="$nexaSecondary">
                      Ref: {item.event_id ? item.event_id.slice(0, 12) : 'EV-0000'}
                    </Text>
                  </XStack>
                </YStack>
              </Surface>
            )
          })
        ) : (
          <Surface
            padding="$6"
            borderRadius={16}
            alignItems="center"
            justifyContent="center"
            elevation="$1"
          >
            <YStack alignItems="center" gap="$3" maxWidth={420} paddingVertical="$4">
              <Activity size={48} color="$nexaAccent" />
              <Text fontSize={18} fontWeight="800" color="$nexaText" textAlign="center">
                No health events recorded yet
              </Text>
              <Paragraph color="$nexaSecondary" fontSize={14} textAlign="center">
                Medical reports, prescriptions, and vital readings processed by your care team will appear here in chronological order.
              </Paragraph>
              <ActionButton
                intent="primary"
                onPress={() => router.push('/patient/dashboard')}
              >
                Return to Dashboard
              </ActionButton>
            </YStack>
          </Surface>
        )}
      </YStack>
    </YStack>
  )
}
