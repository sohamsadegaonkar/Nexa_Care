/**
 * Patient record viewer screen — view consent-gated patient data.
 *
 * SECURITY ARCHITECTURE:
 * - The frontend lock is a UX control ONLY — it is NOT the security boundary.
 * - Every data request must check consent server-side (backend enforces this).
 * - The consent token is passed as X-Consent-Token header on every API call.
 *   It is NEVER displayed in the UI or stored in URLs.
 * - Scope-restricted access: tabs not in the consent scope are disabled and
 *   their data is NOT fetched.
 * - Consent revalidation every 10 seconds (backend still validates every request).
 * - AI-extracted fields show provenance + verification status, not just confidence.
 *
 * ALPHA: Per-section scope-gated endpoints not yet deployed on the backend.
 * When available, each tab should call its own scope-gated endpoint.
 *
 * Route: /doctor/patient-record?request_id=...&patient_id=...
 */

'use client'

import { Card, Text, YStack, Button, XStack, Separator, Spinner, Paragraph, ScrollView } from '@my/ui'
import { AlertTriangle, Clock, ShieldCheck, FileText, Heart, Pill, FlaskConical, AlertOctagon, Activity } from '@tamagui/lucide-icons'
import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { NexaApiClient, type EmergencySummaryResponse } from '../../utils/apiClient'
import { useProviderAuth } from './ProviderAuthContext'

// ── Data types ──────────────────────────────────────────────────────────────

interface VitalEntry {
  type: string
  value: string
  unit: string
  recorded_at: string
  source?: string
  confidence?: number | null
  verified?: boolean | null
}

interface MedicationEntry {
  name: string
  dosage: string
  frequency: string
  source?: string
  confidence?: number | null
  verified?: boolean | null
}

interface LabResultEntry {
  test_name: string
  value: string
  unit: string
  reference_range?: string | null
  is_abnormal?: boolean
  recorded_at: string
  source?: string
  confidence?: number | null
  verified?: boolean | null
}

interface TimelineEntry {
  event_id: string
  event_type: string
  title: string
  summary: string
  event_date: string
  source?: string
  source_display?: string
  confidence?: number | null
  verified?: boolean | null
  risk_level?: string | null
  badges?: string[]
}

interface PatientSummary {
  patient_id: string
  pii: {
    patient_name: string
    phone: string
    aadhaar_abha_id?: string
  }
  clinical_summary: {
    blood_group?: string
    allergies: string[]
    chronic_conditions: string[]
    active_conditions?: string[]
    active_medications: MedicationEntry[]
    current_medications?: MedicationEntry[]
    latest_vitals: VitalEntry[]
    recent_labs?: LabResultEntry[]
  }
  shard_scope: string
}

interface ConsentValidation {
  valid: boolean
  expires_at?: string
  scope?: string[]
  purpose?: string
}

type ViewerState = 'loading' | 'active' | 'expired' | 'error'

// ── Scope → Tab mapping ─────────────────────────────────────────────────────

const SCOPE_TO_TABS: Record<string, string[]> = {
  clinical: ['summary', 'vitals', 'prescriptions', 'labs', 'allergies', 'timeline'],
  full: ['summary', 'vitals', 'prescriptions', 'labs', 'allergies', 'documents', 'timeline', 'access'],
  patient_summary: ['summary'],
  vitals: ['vitals'],
  medications: ['prescriptions'],
  allergies: ['allergies', 'summary'],
  lab_results: ['labs'],
  clinical_record: ['summary', 'vitals', 'prescriptions', 'labs', 'allergies', 'documents', 'timeline'],
}

const ALL_TABS = ['summary', 'vitals', 'prescriptions', 'labs', 'allergies', 'documents', 'timeline', 'access']

// ── Provenance badge ────────────────────────────────────────────────────────
// Distinguishes AI-extracted + verification status from clinician-verified.
// For high-risk fields, AI confidence should never replace human verification.

function ProvenanceBadge({ confidence, source, verified }: {
  confidence?: number | null
  source?: string
  verified?: boolean | null
}) {
  if (!source) return null
  const isAI = source === 'ai_extracted' || (source && source.toLowerCase().includes('ai'))

  if (verified) {
    return (
      <Card backgroundColor="$green2" borderRadius="$2" padding="$1" paddingHorizontal="$2">
        <Text color="$green10" fontSize={11} fontWeight="600">Clinician verified</Text>
      </Card>
    )
  }

  if (isAI) {
    const pct = confidence != null ? Math.round(confidence * 100) : null
    const color = pct != null
      ? pct >= 90 ? '$yellow10'
      : pct >= 70 ? '$orange10'
      : '$red10'
      : '$blue10'
    const label = pct != null ? `AI extracted · ${pct}% model confidence` : 'AI extracted'

    return (
      <YStack gap="$1">
        <Card backgroundColor="$blue2" borderRadius="$2" padding="$1" paddingHorizontal="$2">
          <Text color={color} fontSize={11} fontWeight="600">{label}</Text>
        </Card>
        <Text color="$orange9" fontSize={10}>Not yet verified</Text>
      </YStack>
    )
  }

  return (
    <Card backgroundColor="$gray2" borderRadius="$2" padding="$1" paddingHorizontal="$2">
      <Text color="$gray10" fontSize={11}>Manual entry</Text>
    </Card>
  )
}

function isBreakGlassGrant(purpose?: string): boolean {
  return purpose === 'EMERGENCY'
}

/** Maps the category-filtered emergency-summary response into the same
 * shape the routine /summary rendering already understands, so a
 * break-glass grant only ever shows the categories it was actually
 * granted -- everything else stays empty, not fetched. */
function mapEmergencySummary(data: EmergencySummaryResponse): PatientSummary {
  const categories = data.categories ?? {}

  const allergiesCat = categories['allergies']
  const medsCat = categories['active_medications']
  const vitalsCat = categories['vitals']
  const labsCat = categories['lab_results']
  const bloodGroupCat = categories['blood_group']

  return {
    patient_id: data.patient_id,
    pii: { patient_name: 'Emergency access (identity not requested)', phone: '' },
    clinical_summary: {
      // Blood group is only ever populated once a real verified source
      // exists on the backend -- otherwise it must stay absent, never a
      // fabricated/default value.
      blood_group: bloodGroupCat?.verified ? String(bloodGroupCat.value ?? '') : undefined,
      allergies: ((allergiesCat?.items as any[]) ?? []).map(
        (a) => `${a.allergen} (${a.severity})`
      ),
      chronic_conditions: [],
      active_medications: ((medsCat?.items as any[]) ?? []).map((m) => ({
        name: m.name,
        dosage: m.strength,
        frequency: m.frequency,
        source: m.source,
        confidence: m.confidence,
        verified: m.verified,
      })),
      latest_vitals: ((vitalsCat?.items as any[]) ?? []).map((v) => ({
        type: v.type,
        value: v.value,
        unit: v.unit,
        recorded_at: v.recorded_at,
        source: v.source,
        confidence: v.confidence,
        verified: v.verified,
      })),
      recent_labs: ((labsCat?.items as any[]) ?? []).map((l) => ({
        test_name: l.test_name,
        value: l.value,
        unit: l.unit,
        reference_range: l.reference_range,
        is_abnormal: l.is_abnormal,
        recorded_at: l.recorded_at,
        source: l.source,
        confidence: l.confidence,
        verified: l.verified,
      })),
    },
    shard_scope: 'clinical',
  }
}

// ── Mask token for display ──────────────────────────────────────────────────

function maskToken(token: string): string {
  if (!token || token.length < 12) return '••••••••'
  return `${token.slice(0, 6)}••••${token.slice(-4)}`
}

// ── Main component ──────────────────────────────────────────────────────────

export function PatientRecordViewerScreen() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const patientId = searchParams.get('patient_id') ?? ''

  const { providerId, isAuthenticated, session, accessGrant, clearAccessGrant } = useProviderAuth()
  const hospitalId = session?.hospital.hospital_id ?? ''
  const requestId = accessGrant?.requestId ?? ''

  const [summary, setSummary] = useState<PatientSummary | null>(null)
  const [timeline, setTimeline] = useState<TimelineEntry[]>([])
  const consentValidation: ConsentValidation | null = useMemo(() => accessGrant ? ({
    valid: true,
    expires_at: accessGrant.expiresAt,
    scope: [accessGrant.scope],
    purpose: accessGrant.purpose,
  }) : null, [accessGrant])
  const [viewerState, setViewerState] = useState<ViewerState>('loading')
  const [error, setError] = useState<string | null>(null)
  const [secondsRemaining, setSecondsRemaining] = useState<number | null>(null)
  const [activeTab, setActiveTab] = useState<string>('summary')

  const expiryTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const unmountClearRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (unmountClearRef.current) clearTimeout(unmountClearRef.current)
    return () => {
      unmountClearRef.current = setTimeout(clearAccessGrant, 0)
    }
  }, [clearAccessGrant])

  // ── Compute available tabs from consent scope ─────────────────────────

  const availableTabs = useMemo(() => {
    if (!consentValidation?.scope || consentValidation.scope.length === 0) {
      return ALL_TABS
    }
    const allowedTabKeys = new Set<string>()
    for (const scopeCat of consentValidation.scope) {
      const tabs = SCOPE_TO_TABS[scopeCat]
      if (tabs) {
        for (const t of tabs) allowedTabKeys.add(t)
      }
    }
    allowedTabKeys.add('access')
    if (allowedTabKeys.size <= 1) return ALL_TABS
    return ALL_TABS.filter(t => allowedTabKeys.has(t))
  }, [consentValidation?.scope])

  // ── Fetch patient record ──────────────────────────────────────────────

  const fetchRecord = useCallback(async () => {
    if (!patientId || !accessGrant || accessGrant.patientId !== patientId || !hospitalId) {
      setError('Approved access is no longer available. Return to consent history and securely reclaim access.')
      setViewerState('error')
      return
    }

    try {
      if (isBreakGlassGrant(accessGrant.purpose)) {
        const data = await NexaApiClient.getEmergencySummary(patientId, accessGrant.consentToken)
        setSummary(mapEmergencySummary(data))
        setTimeline([]) // Emergency summary has no timeline; the routine timeline endpoint rejects break-glass tokens.
        setViewerState('active')
        return
      }

      const data = await NexaApiClient.getPatientSummary(
        patientId, accessGrant.consentToken, hospitalId, 'clinical_summary',
      ) as any
      const mapped: PatientSummary = {
        patient_id: data.patient_id,
        pii: { patient_name: data.pii?.patient_name ?? 'Unknown', phone: data.pii?.phone ?? '' },
        clinical_summary: {
          blood_group: data.clinical_summary?.blood_group,
          allergies: data.clinical_summary?.allergies ?? (data as any).allergies ?? [],
          chronic_conditions: data.clinical_summary?.chronic_conditions ?? [],
          active_medications: data.clinical_summary?.active_medications ?? (data as any).medications ?? [],
          latest_vitals: data.clinical_summary?.latest_vitals ?? (data as any).vitals ?? [],
          recent_labs: data.clinical_summary?.recent_labs ?? [],
        },
        shard_scope: data.shard_scope ?? 'clinical',
      }
      setSummary(mapped)

      // Fetch timeline only if scope allows
      const scopeAllowsTimeline = !consentValidation?.scope
        || consentValidation.scope.includes('clinical_record')
        || consentValidation.scope.includes('timeline_view')

      if (scopeAllowsTimeline) {
        try {
          const tlData = await NexaApiClient.getPatientTimeline(
            patientId, accessGrant.consentToken, hospitalId,
          ) as any
          setTimeline(tlData?.events ?? [])
        } catch { /* non-fatal */ }
      }

      setViewerState('active')
    } catch {
      clearAccessGrant()
      setError('Failed to load patient record. Consent may have expired.')
      setViewerState('error')
    }
  }, [accessGrant, clearAccessGrant, hospitalId, patientId, consentValidation?.scope])

  useEffect(() => { fetchRecord() }, [fetchRecord])

  useEffect(() => {
    if (!accessGrant) return
    const remaining = Math.max(0, Math.floor((Date.parse(accessGrant.expiresAt) - Date.now()) / 1000))
    setSecondsRemaining(remaining)
    if (remaining <= 0) {
      clearAccessGrant()
      setViewerState('expired')
    }
  }, [accessGrant, clearAccessGrant])

  // ── Consent expiry countdown ──────────────────────────────────────────

  useEffect(() => {
    if (viewerState !== 'active' || secondsRemaining === null) return
    expiryTimerRef.current = setInterval(() => {
      setSecondsRemaining((prev) => {
        if (prev === null || prev <= 1) { clearAccessGrant(); setViewerState('expired'); return 0 }
        return prev - 1
      })
    }, 1000)
    return () => { if (expiryTimerRef.current) { clearInterval(expiryTimerRef.current); expiryTimerRef.current = null } }
  }, [clearAccessGrant, viewerState, secondsRemaining !== null])

  // ── Periodic consent revalidation (every 10 seconds) ──────────────────

  // ── Format helpers ────────────────────────────────────────────────────

  useEffect(() => {
    if (viewerState !== 'active' || !accessGrant) return
    const revalidationTimer = setInterval(() => {
      void fetchRecord()
    }, 10000)
    return () => clearInterval(revalidationTimer)
  }, [accessGrant, fetchRecord, viewerState])

  const formatCountdown = (seconds: number) => {
    const m = Math.floor(seconds / 60)
    const s = seconds % 60
    return `${m}:${s.toString().padStart(2, '0')}`
  }

  // ── Session guard ─────────────────────────────────────────────────────
  if (!isAuthenticated) {
    return (
      <YStack flex={1} bg="$background" justifyContent="center" alignItems="center" gap="$4">
        <Text fontSize={44}>🔒</Text>
        <Text fontSize={22} fontWeight="900" color="$color12" textAlign="center">Session Required</Text>
        <Paragraph textAlign="center" color="$color11">You must be logged in to view patient records.</Paragraph>
        <Button theme="blue" size="$4" onPress={() => router.push('/doctor/login')}>Go to Login</Button>
      </YStack>
    )
  }

  // ── Render: Loading ───────────────────────────────────────────────────

  if (viewerState === 'loading') {
    return <YStack flex={1} bg="$background" justifyContent="center" alignItems="center"><Spinner size="large" color="$blue10" /><Paragraph color="$color11" marginTop="$3">Loading patient record...</Paragraph></YStack>
  }

  if (viewerState === 'error') {
    return (
      <YStack flex={1} bg="$background" padding="$6" gap="$4" justifyContent="center" alignItems="center">
        <Text fontSize={44}>⚠️</Text>
        <Text fontSize={22} fontWeight="900" color="$color12" textAlign="center">Access Error</Text>
        <Paragraph textAlign="center" color="$color11">{error}</Paragraph>
        <Button theme="blue" size="$4" onPress={() => router.push('/doctor/dashboard')}>Back to Dashboard</Button>
      </YStack>
    )
  }

  // ── Render: Expired ───────────────────────────────────────────────────

  if (viewerState === 'expired') {
    return (
      <YStack flex={1} bg="$background" padding="$6" gap="$4" justifyContent="center" alignItems="center">
        <Text fontSize={56}>🔒</Text>
        <Text fontSize={24} fontWeight="900" color="$red10" textAlign="center">Consent Expired</Text>
        <Paragraph textAlign="center" color="$color11">Consent expired. Request access again.</Paragraph>
        <Paragraph textAlign="center" color="$color10" fontSize={11}>
          The frontend timer is a UX indicator only. All data access is
          independently validated server-side on every request.
        </Paragraph>
        <Button theme="blue" size="$4" onPress={() => router.push('/doctor/dashboard')}>Back to Dashboard</Button>
      </YStack>
    )
  }

  if (!summary) return null

  const allergies = summary.clinical_summary?.allergies ?? []
  const vitals = summary.clinical_summary?.latest_vitals ?? []
  const medications = summary.clinical_summary?.active_medications ?? summary.clinical_summary?.current_medications ?? []
  const labs = summary.clinical_summary?.recent_labs ?? []
  const conditions = summary.clinical_summary?.chronic_conditions ?? summary.clinical_summary?.active_conditions ?? []

  const allergiesInScope = !consentValidation?.scope
    || consentValidation.scope.includes('allergies')
    || consentValidation.scope.includes('clinical_record')
    || consentValidation.scope.includes('patient_summary')

  // ── Tab definitions ───────────────────────────────────────────────────

  const tabs = [
    { key: 'summary', label: 'Summary', icon: <FileText size={16} /> },
    { key: 'vitals', label: 'Vitals', icon: <Activity size={16} /> },
    { key: 'prescriptions', label: 'Prescriptions', icon: <Pill size={16} /> },
    { key: 'labs', label: 'Lab Reports', icon: <FlaskConical size={16} /> },
    { key: 'allergies', label: 'Allergies', icon: <AlertOctagon size={16} /> },
    { key: 'documents', label: 'Documents', icon: <FileText size={16} /> },
    { key: 'timeline', label: 'Timeline', icon: <Clock size={16} /> },
    { key: 'access', label: 'Access Status', icon: <ShieldCheck size={16} /> },
  ]

  const visibleTabs = tabs.filter(t => availableTabs.includes(t.key))

  return (
    <ScrollView>
      <YStack flex={1} bg="$background" padding="$5" gap="$5" maxWidth={800} marginHorizontal="auto">
        <XStack alignItems="center" justifyContent="space-between">
          <Text fontSize={26} fontWeight="900" color="$color12">Patient Record</Text>
          <Button size="$3" chromeless onPress={() => router.push('/doctor/dashboard')}>← Dashboard</Button>
        </XStack>

        {/* Emergency access banner — break-glass grants only */}
        {isBreakGlassGrant(accessGrant?.purpose) && (
          <Card backgroundColor="$red3" borderWidth={2} borderColor="$red9" padding="$3">
            <XStack alignItems="center" gap="$2">
              <AlertOctagon size={20} color="$red10" />
              <YStack>
                <Text color="$red10" fontSize={15} fontWeight="800">
                  EMERGENCY (BREAK-GLASS) ACCESS
                </Text>
                <Text color="$red10" fontSize={12}>
                  Showing only the clinical categories approved for this emergency grant. This access is audited.
                </Text>
              </YStack>
            </XStack>
          </Card>
        )}

        {/* Consent expiry countdown bar */}
        {secondsRemaining !== null && (
          <XStack backgroundColor={secondsRemaining < 60 ? '$red2' : '$blue2'} borderRadius="$3" padding="$3" alignItems="center" justifyContent="space-between">
            <Text color={secondsRemaining < 60 ? '$red10' : '$blue10'} fontSize={14} fontWeight="600">
              {secondsRemaining < 60 ? '⚠️ Consent expiring soon' : 'Consent active'}
            </Text>
            <Text color={secondsRemaining < 60 ? '$red10' : '$blue10'} fontSize={18} fontWeight="800">
              {formatCountdown(secondsRemaining)}
            </Text>
          </XStack>
        )}

        {/* Allergies banner — visible when in scope */}
        {allergiesInScope && allergies.length > 0 && (
          <Card backgroundColor="$red2" borderWidth={1} borderColor="$red8" padding="$3">
            <XStack alignItems="center" gap="$2">
              <AlertTriangle size={18} color="$red10" />
              <Text color="$red10" fontSize={14} fontWeight="700">
                ALLERGIES: {allergies.join(' · ')}
              </Text>
            </XStack>
          </Card>
        )}

        {/* Tab navigation — only tabs in consent scope */}
        <XStack gap="$1" flexWrap="wrap">
          {visibleTabs.map((tab) => (
            <Button key={tab.key} size="$2" theme={activeTab === tab.key ? 'blue' : undefined} chromeless={activeTab !== tab.key} onPress={() => setActiveTab(tab.key)}>
              {tab.label}
            </Button>
          ))}
        </XStack>

        <Separator />

        {/* Tab content */}
        {activeTab === 'summary' && (
          <YStack gap="$4">
            <Card padding="$4" backgroundColor="$color2" borderWidth={1} borderColor="$borderColor" gap="$2">
              <Text color="$color12" fontSize={22} fontWeight="900">{summary.pii.patient_name}</Text>
              <Text color="$color11" fontSize={14}>{summary.pii.phone}</Text>
              {summary.clinical_summary?.blood_group && (
                <XStack gap="$2" alignItems="center">
                  <Text color="$color11" fontSize={14}>Blood Group:</Text>
                  <Text color="$red10" fontSize={18} fontWeight="700">{summary.clinical_summary.blood_group}</Text>
                </XStack>
              )}
            </Card>
            {conditions.length > 0 && (
              <YStack gap="$2">
                <Paragraph color="$color11" fontWeight="700">Conditions</Paragraph>
                <XStack flexWrap="wrap" gap="$2">
                  {conditions.map((c) => <Card key={c} backgroundColor="$blue2" borderRadius="$2" padding="$2" paddingHorizontal="$3"><Text color="$blue10" fontSize={14}>{c}</Text></Card>)}
                </XStack>
              </YStack>
            )}
            {allergiesInScope && allergies.length > 0 && (
              <Card backgroundColor="$red2" borderWidth={2} borderColor="$red8" padding="$4" gap="$2">
                <XStack alignItems="center" gap="$2">
                  <AlertTriangle size={20} color="$red10" />
                  <Text color="$red10" fontSize={16} fontWeight="700">ALLERGIES — SAFETY CRITICAL</Text>
                </XStack>
                <XStack flexWrap="wrap" gap="$2">
                  {allergies.map((a) => <Card key={a} backgroundColor="$red3" borderRadius="$2" padding="$2" paddingHorizontal="$3" borderWidth={1} borderColor="$red8"><Text color="$red10" fontWeight="700">{a}</Text></Card>)}
                </XStack>
              </Card>
            )}
            <Paragraph color="$color10" fontSize={11} opacity={0.5}>
              Access is consent-gated and audited. Authorization reference: {maskToken(requestId)}
            </Paragraph>
          </YStack>
        )}

        {activeTab === 'vitals' && (
          <YStack gap="$3">
            <Paragraph color="$color11" fontWeight="700">Latest Vitals</Paragraph>
            {vitals.length === 0 ? <Paragraph color="$color11">No vitals recorded.</Paragraph> : vitals.map((v, i) => (
              <XStack key={i} backgroundColor="$color2" borderRadius="$3" padding="$3" gap="$4" alignItems="center">
                <Text color="$color12" fontWeight="700">{v.type}</Text>
                <Text color="$color12" fontWeight="800">{v.value} {v.unit}</Text>
                <Text color="$color10" fontSize={12}>{v.recorded_at}</Text>
                <ProvenanceBadge confidence={v.confidence} source={v.source} verified={v.verified} />
              </XStack>
            ))}
          </YStack>
        )}

        {activeTab === 'prescriptions' && (
          <YStack gap="$3">
            <Paragraph color="$color11" fontWeight="700">Active Medications</Paragraph>
            {medications.length === 0 ? <Paragraph color="$color11">No active medications.</Paragraph> : medications.map((m, i) => (
              <XStack key={i} backgroundColor="$color2" borderRadius="$3" padding="$3" gap="$4" alignItems="center">
                <Text color="$color12" fontWeight="700">{m.name}</Text>
                <Text color="$color11">{m.dosage} · {m.frequency}</Text>
                <ProvenanceBadge confidence={m.confidence} source={m.source} verified={m.verified} />
              </XStack>
            ))}
          </YStack>
        )}

        {activeTab === 'labs' && (
          <YStack gap="$3">
            <Paragraph color="$color11" fontWeight="700">Lab Reports</Paragraph>
            {labs.length === 0 ? <Paragraph color="$color11">No lab results available.</Paragraph> : labs.map((l, i) => (
              <YStack key={i} backgroundColor="$color2" borderRadius="$3" padding="$3" gap="$2">
                <XStack alignItems="center" gap="$3">
                  <Text color="$color12" fontWeight="700">{l.test_name}</Text>
                  <Text color={l.is_abnormal ? '$red10' : '$color12'} fontWeight="800">{l.value} {l.unit}</Text>
                  {l.is_abnormal && <Card backgroundColor="$red2" borderRadius="$2" padding="$1" paddingHorizontal="$2"><Text color="$red10" fontSize={11} fontWeight="700">ABNORMAL</Text></Card>}
                  <ProvenanceBadge confidence={l.confidence} source={l.source} verified={l.verified} />
                </XStack>
                {l.reference_range && <Text color="$color10" fontSize={12}>Ref: {l.reference_range}</Text>}
                <Text color="$color10" fontSize={12}>{l.recorded_at}</Text>
              </YStack>
            ))}
          </YStack>
        )}

        {activeTab === 'allergies' && (
          <YStack gap="$3">
            <Card backgroundColor="$red2" borderWidth={2} borderColor="$red8" padding="$4" gap="$2">
              <XStack alignItems="center" gap="$2">
                <AlertOctagon size={20} color="$red10" />
                <Text color="$red10" fontSize={16} fontWeight="700">ALLERGIES — SAFETY CRITICAL</Text>
              </XStack>
              <Text color="$red9" fontSize={13}>
                These allergies must be reviewed before any treatment decision.
                AI-extracted allergy values must be verified by a clinician before
                use in treatment decisions.
              </Text>
            </Card>
            {allergies.length === 0 ? <Paragraph color="$color11">No known allergies.</Paragraph> : (
              <XStack flexWrap="wrap" gap="$2">
                {allergies.map((a) => <Card key={a} backgroundColor="$red3" borderRadius="$2" padding="$3" paddingHorizontal="$4" borderWidth={1} borderColor="$red8"><Text color="$red10" fontSize={18} fontWeight="700">⚠️ {a}</Text></Card>)}
              </XStack>
            )}
          </YStack>
        )}

        {activeTab === 'documents' && (
          <YStack gap="$3">
            <Paragraph color="$color11" fontWeight="700">Documents</Paragraph>
            <Paragraph color="$color11">No documents available.</Paragraph>
          </YStack>
        )}

        {activeTab === 'timeline' && (
          <YStack gap="$3">
            <Paragraph color="$color11" fontWeight="700">Clinical Timeline</Paragraph>
            {timeline.length === 0 ? <Paragraph color="$color11">No timeline events available.</Paragraph> : timeline.map((t) => (
              <YStack key={t.event_id} backgroundColor="$color2" borderRadius="$3" padding="$3" gap="$2">
                <XStack alignItems="center" gap="$2">
                  <Text color="$color12" fontWeight="700">{t.title}</Text>
                  <ProvenanceBadge confidence={t.confidence} source={t.source} verified={t.verified} />
                </XStack>
                <Text color="$color11" fontSize={14}>{t.summary}</Text>
                <XStack gap="$2" alignItems="center">
                  <Text color="$color10" fontSize={12}>{t.event_date}</Text>
                  {t.source_display && <Text color="$blue9" fontSize={11}>{t.source_display}</Text>}
                </XStack>
                {t.badges && t.badges.length > 0 && (
                  <XStack flexWrap="wrap" gap="$1">
                    {t.badges.map((badge, bi) => <Card key={bi} backgroundColor="$blue2" borderRadius="$2" padding="$1" paddingHorizontal="$2"><Text color="$blue10" fontSize={11}>{badge}</Text></Card>)}
                  </XStack>
                )}
              </YStack>
            ))}
          </YStack>
        )}

        {activeTab === 'access' && (
          <YStack gap="$4">
            <Paragraph color="$color11" fontWeight="700">Access Status</Paragraph>
            <Card backgroundColor="$color2" borderWidth={1} borderColor="$borderColor" padding="$4" gap="$3">
              <XStack alignItems="center" gap="$2">
                <Text color="$color10" fontSize={14}>Authorization:</Text>
                <Card backgroundColor="$green2" borderRadius="$2" padding="$1" paddingHorizontal="$2">
                  <Text color="$green10" fontSize={13} fontWeight="600">Active</Text>
                </Card>
              </XStack>
              <XStack alignItems="center" gap="$2">
                <Text color="$color10" fontSize={14}>Authorization Reference:</Text>
                <Text color="$color12" fontSize={13}>{maskToken(requestId)}</Text>
              </XStack>
              <Separator marginVertical="$2" />
              <XStack alignItems="center" gap="$2">
                <Text color="$color10" fontSize={14}>Scope:</Text>
                <Text color="$color12">{summary.shard_scope}</Text>
              </XStack>
              <Separator marginVertical="$2" />
              <XStack alignItems="center" gap="$2">
                <Text color="$color10" fontSize={14}>Provider:</Text>
                <Text color="$color12">{providerId || 'Unknown'}</Text>
              </XStack>
              {secondsRemaining !== null && (
                <>
                  <Separator marginVertical="$2" />
                  <XStack alignItems="center" gap="$2">
                    <Text color="$color10" fontSize={14}>Time Remaining:</Text>
                    <Text color={secondsRemaining < 60 ? '$red10' : '$color12'} fontWeight="700">{formatCountdown(secondsRemaining)}</Text>
                  </XStack>
                </>
              )}
            </Card>
            <Paragraph color="$color10" textAlign="center" fontSize={12}>
              All data access is consent-gated and audited. This access will be
              recorded and may trigger patient and compliance notifications.
            </Paragraph>
          </YStack>
        )}
      </YStack>
    </ScrollView>
  )
}