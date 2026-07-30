/**
 * Request consent screen — provider initiates a consent request.
 *
 * Uses the session's real provider_id from ProviderAuthContext.
 * Patient ID comes from route params (selected from search).
 * Calls POST /api/v2/consent/request to create the challenge.
 *
 * SECURITY: This screen NEVER calls any approval/respond endpoint.
 * Only the patient can approve or deny — the doctor can only request.
 *
 * GOVERNANCE:
 * - Purpose is a controlled code (not free-text) — backend validates.
 * - Scope is a controlled category (not free-text) — backend validates.
 * - Duration is a preset selection — backend clamps to [300, 3600].
 * - provider_id is sent in body for API compatibility, but the server
 *   derives it from the authenticated session and rejects mismatches.
 *
 * Route: /doctor/request-consent?patient_id=...
 */

'use client'

import { Card, Text, YStack, Button, Input, XStack, Spinner, Paragraph, ScrollView } from '@my/ui'
import { useRef, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { ApiError, NexaApiClient } from '../../utils/apiClient'
import { useProviderAuth } from './ProviderAuthContext'
import { ConsentSelect } from './components/ConsentSelect'

// ── Controlled purpose codes ────────────────────────────────────────────────
type AccessPurpose =
  | 'treatment'
  | 'emergency_care'
  | 'diagnostic_review'
  | 'follow_up'
  | 'referral'
  | 'document_processing'

const PURPOSE_OPTIONS: { value: AccessPurpose; label: string; description: string }[] = [
  { value: 'treatment', label: 'Treatment', description: 'Direct clinical treatment' },
  { value: 'emergency_care', label: 'Emergency Care', description: 'Urgent medical attention' },
  { value: 'diagnostic_review', label: 'Diagnostic Review', description: 'Reviewing test results' },
  { value: 'follow_up', label: 'Follow-up', description: 'Post-visit follow-up' },
  { value: 'referral', label: 'Referral', description: 'Specialist referral review' },
]

// ── Controlled scope categories ──────────────────────────────────────────────
type ConsentScope = 'clinical' | 'full' | 'documents'

const SCOPE_OPTIONS: { value: ConsentScope; label: string; description: string }[] = [
  {
    value: 'clinical',
    label: 'Clinical',
    description: 'Clinical information required for this care workflow',
  },
  { value: 'full', label: 'Full Record', description: 'Complete patient record access' },
]

// ── Duration presets (server clamps to [5 min, 60 min]) ─────────────────────
const DURATION_PRESETS = [
  { value: 300, label: '5 minutes' },
  { value: 900, label: '15 minutes' },
  { value: 1800, label: '30 minutes' },
  { value: 3600, label: '60 minutes' },
] as const

export function RequestConsentScreen() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const patientId = searchParams.get('patient_id') ?? ''
  const documentUploadIntent = searchParams.get('intent') === 'document_upload'
  const { providerId, hospitalName, isAuthenticated, session } = useProviderAuth()

  // ── Session guard ─────────────────────────────────────────────────────
  if (!isAuthenticated) {
    return (
      <YStack
        flex={1}
        bg="$background"
        justifyContent="center"
        alignItems="center"
        gap="$4"
      >
        <Text fontSize={44}>🔒</Text>
        <Text
          fontSize={22}
          fontWeight="900"
          color="$color12"
          textAlign="center"
        >
          Session Required
        </Text>
        <Paragraph
          textAlign="center"
          color="$color11"
        >
          You must be logged in to request consent.
        </Paragraph>
        <Button
          theme="blue"
          size="$4"
          onPress={() => router.push('/doctor/login')}
        >
          Go to Login
        </Button>
      </YStack>
    )
  }

  const [purpose, setPurpose] = useState<AccessPurpose>(
    documentUploadIntent ? 'document_processing' : 'treatment'
  )
  const [purposeNote, setPurposeNote] = useState('')
  const [requestedScope, setRequestedScope] = useState<ConsentScope>(
    documentUploadIntent ? 'documents' : 'clinical'
  )
  const [accessDuration, setAccessDuration] = useState(900)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const submissionInFlight = useRef(false)

  const handleSubmit = async () => {
    if (!patientId) {
      setError('No patient selected.')
      return
    }
    const hospitalId = session?.hospital.hospital_id
    if (!hospitalId) {
      setError('Provider hospital context is unavailable. Sign in again.')
      return
    }
    if (submissionInFlight.current) return
    submissionInFlight.current = true
    setSubmitting(true)
    setError(null)
    try {
      const data = await NexaApiClient.requestConsent(
        {
          patient_id: patientId,
          provider_id: providerId ?? '',
          purpose: documentUploadIntent ? 'document_processing' : purpose,
          scope: documentUploadIntent ? 'documents' : requestedScope,
          access_duration_seconds: accessDuration,
          ...(purposeNote.trim() ? { purpose_note: purposeNote.trim() } : {}),
        },
        hospitalId
      )
      router.push(
        `/doctor/waiting?request_id=${encodeURIComponent(data.request_id)}&patient_id=${encodeURIComponent(patientId)}${documentUploadIntent ? '&intent=document_upload' : ''}`
      )
    } catch (caught: unknown) {
      setError(
        caught instanceof ApiError
          ? `Consent request failed: ${caught.message}`
          : 'Failed to create consent request. Please try again.'
      )
    } finally {
      submissionInFlight.current = false
      setSubmitting(false)
    }
  }

  const formatDuration = (seconds: number) => {
    const m = Math.ceil(seconds / 60)
    return `${m} minute${m !== 1 ? 's' : ''}`
  }

  return (
    <ScrollView>
      <YStack
        flex={1}
        bg="$background"
        padding="$5"
        gap="$5"
        maxWidth={600}
        marginHorizontal="auto"
      >
        <YStack>
          <Text
            fontSize={26}
            fontWeight="900"
            color="$color12"
          >
            Request Consent
          </Text>
          <Paragraph
            color="$color11"
            fontSize={15}
          >
            Request access to patient health data. The patient will receive a push notification to
            approve or deny.
          </Paragraph>
        </YStack>

        {/* Patient info */}
        <Card
          padding="$4"
          backgroundColor="$color2"
          borderWidth={1}
          borderColor="$borderColor"
          gap="$2"
        >
          <Paragraph
            color="$color10"
            fontSize={12}
            fontWeight="700"
            textTransform="uppercase"
          >
            Patient
          </Paragraph>
          <Text
            color="$color12"
            fontSize={18}
            fontWeight="700"
          >
            {patientId || 'No patient selected'}
          </Text>
        </Card>

        {/* Provider info */}
        <Card
          padding="$4"
          backgroundColor="$color2"
          borderWidth={1}
          borderColor="$borderColor"
          gap="$2"
        >
          <Paragraph
            color="$color10"
            fontSize={12}
            fontWeight="700"
            textTransform="uppercase"
          >
            Provider
          </Paragraph>
          <Text
            color="$color12"
            fontSize={16}
            fontWeight="600"
          >
            {providerId || 'Unknown'} · {hospitalName || 'Hospital'}
          </Text>
        </Card>

        {/* Purpose is immutable for document ingestion. */}
        {documentUploadIntent ? (
          <Card
            padding="$4"
            backgroundColor="$blue3"
            gap="$2"
          >
            <Paragraph
              color="$color10"
              fontSize={12}
              fontWeight="700"
            >
              LOCKED PURPOSE
            </Paragraph>
            <Text
              color="$color12"
              fontSize={16}
              fontWeight="700"
            >
              Document processing
            </Text>
            <Paragraph
              color="$color10"
              fontSize={13}
            >
              This grant can authorize document upload, extraction results, and source adjudication
              only.
            </Paragraph>
          </Card>
        ) : (
          <YStack gap="$2">
            <ConsentSelect
              id="consent-purpose"
              label="Purpose"
              value={purpose}
              options={PURPOSE_OPTIONS}
              onValueChange={setPurpose}
              disabled={submitting}
            />
            <Paragraph
              color="$color10"
              fontSize={13}
            >
              {PURPOSE_OPTIONS.find((o) => o.value === purpose)?.description}
            </Paragraph>
          </YStack>
        )}

        {/* Purpose note (optional free-text explanation) */}
        {!documentUploadIntent && (
          <YStack gap="$2">
            <Paragraph
              color="$color11"
              fontSize={15}
            >
              Purpose Note (optional)
            </Paragraph>
            <Input
              size="$4"
              value={purposeNote}
              onChangeText={setPurposeNote}
              placeholder="e.g. Diabetes follow-up consultation"
            />
          </YStack>
        )}

        {/* Scope is immutable for document ingestion. */}
        {documentUploadIntent ? (
          <Card
            padding="$4"
            backgroundColor="$blue3"
            gap="$2"
          >
            <Paragraph
              color="$color10"
              fontSize={12}
              fontWeight="700"
            >
              LOCKED SCOPE
            </Paragraph>
            <Text
              color="$color12"
              fontSize={16}
              fontWeight="700"
            >
              Documents
            </Text>
          </Card>
        ) : (
          <YStack gap="$2">
            <ConsentSelect
              id="consent-scope"
              label="Requested Scope"
              value={requestedScope}
              options={SCOPE_OPTIONS}
              onValueChange={setRequestedScope}
              disabled={submitting}
            />
            <Paragraph
              color="$color10"
              fontSize={13}
            >
              {SCOPE_OPTIONS.find((o) => o.value === requestedScope)?.description}
            </Paragraph>
          </YStack>
        )}

        {/* Access duration (preset selector) */}
        <YStack gap="$2">
          <ConsentSelect
            id="consent-duration"
            label="Access Duration"
            value={accessDuration}
            options={DURATION_PRESETS}
            onValueChange={setAccessDuration}
            disabled={submitting}
          />
          <Paragraph
            color="$color10"
            fontSize={13}
          >
            Selected: {formatDuration(accessDuration)}. Server enforces minimum 5 min, maximum 60
            min.
          </Paragraph>
        </YStack>

        {error && (
          <Text
            color="$red10"
            fontSize={14}
          >
            {error}
          </Text>
        )}

        <XStack gap="$3">
          <Button
            theme="blue"
            size="$4"
            disabled={submitting || !patientId}
            onPress={handleSubmit}
          >
            {submitting ? (
              <XStack
                gap="$2"
                alignItems="center"
              >
                <Spinner
                  size="small"
                  color="$blue10"
                />
                <Text color="$color12">Sending request...</Text>
              </XStack>
            ) : (
              'Request Access'
            )}
          </Button>
          <Button
            size="$4"
            chromeless
            onPress={() => router.back()}
          >
            Cancel
          </Button>
        </XStack>
      </YStack>
    </ScrollView>
  )
}
