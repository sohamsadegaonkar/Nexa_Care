/**
 * Emergency access (break-glass) screen — consent bypass for life-threatening situations.
 *
 * SECURITY:
 * - Reason code is a controlled value (not free-text) — expanded beyond acute
 *   emergencies to cover incapacitated patients, unidentified patients, and
 *   system failures. "Other" triggers mandatory compliance review.
 * - Clinical justification is required (minimum 20 chars, no whitespace-only).
 *   "Other" reason requires at least 50 chars.
 * - Break-glass is rate-limited: 3 per provider per hour (server-enforced).
 * - TTL is 15 minutes (server-enforced).
 * - The consent token is NEVER displayed — only a masked reference.
 * - Patient notification is not guaranteed — honest wording used.
 *
 * SECURITY ARCHITECTURE NOTE:
 * - Break-glass should require a high-assurance session (recent MFA).
 *   ALPHA: No re-authentication check yet.
 * - Break-glass should provide minimal necessary access, not full record.
 *   ALPHA: Current implementation grants full record access for 15 minutes.
 *
 * Route: /doctor/emergency-access
 */

'use client'

import { Card, Text, YStack, Button, Input, XStack, Spinner, Paragraph, ScrollView, Select } from '@my/ui'
import { AlertTriangle, ChevronDown, ShieldAlert } from '@tamagui/lucide-icons'
import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { NexaApiClient } from '../../utils/apiClient'
import { useProviderAuth } from './ProviderAuthContext'

// ── Controlled reason codes ─────────────────────────────────────────────────
type BreakGlassReason =
  | 'IMMEDIATE_THREAT_TO_LIFE'
  | 'PATIENT_INCAPACITATED'
  | 'EMERGENCY_DIAGNOSTIC_DECISION'
  | 'EMERGENCY_MEDICATION_SAFETY'
  | 'UNIDENTIFIED_PATIENT'
  | 'SURGICAL_EMERGENCY'
  | 'SEVERE_BLEEDING'
  | 'CARDIAC_ARREST'
  | 'ANAPHYLAXIS'
  | 'ACUTE_RESPIRATORY_FAILURE'
  | 'SYSTEM_OR_CONSENT_SERVICE_UNAVAILABLE'
  | 'OTHER_CLINICALLY_JUSTIFIED_EMERGENCY'

const REASON_OPTIONS: { value: BreakGlassReason; label: string; description: string; isOther?: boolean }[] = [
  { value: 'IMMEDIATE_THREAT_TO_LIFE', label: 'Immediate Threat to Life', description: 'Imminent risk to patient life' },
  { value: 'PATIENT_INCAPACITATED', label: 'Patient Incapacitated', description: 'Patient unable to provide consent (unconscious, confused)' },
  { value: 'EMERGENCY_DIAGNOSTIC_DECISION', label: 'Emergency Diagnostic Decision', description: 'Urgent diagnostic decision requiring record access' },
  { value: 'EMERGENCY_MEDICATION_SAFETY', label: 'Emergency Medication Safety', description: 'Need to check allergies/interactions before emergency medication' },
  { value: 'UNIDENTIFIED_PATIENT', label: 'Unidentified Patient', description: 'Patient identity unknown, record matching needed' },
  { value: 'SURGICAL_EMERGENCY', label: 'Surgical Emergency', description: 'Emergency surgery required' },
  { value: 'SEVERE_BLEEDING', label: 'Severe Bleeding', description: 'Uncontrolled hemorrhage' },
  { value: 'CARDIAC_ARREST', label: 'Cardiac Arrest', description: 'Cardiac arrest or acute coronary syndrome' },
  { value: 'ANAPHYLAXIS', label: 'Anaphylaxis', description: 'Severe allergic reaction' },
  { value: 'ACUTE_RESPIRATORY_FAILURE', label: 'Respiratory Failure', description: 'Acute respiratory distress or failure' },
  { value: 'SYSTEM_OR_CONSENT_SERVICE_UNAVAILABLE', label: 'System / Consent Service Down', description: 'Consent service unavailable — continuity of care' },
  { value: 'OTHER_CLINICALLY_JUSTIFIED_EMERGENCY', label: 'Other Clinically Justified Emergency', description: 'Requires detailed justification and triggers mandatory review', isOther: true },
]

// ── Justification validation ────────────────────────────────────────────────

const MIN_JUSTIFICATION_LENGTH = 20
const OTHER_JUSTIFICATION_LENGTH = 50

function validateJustification(text: string, reasonCode: BreakGlassReason): string | null {
  const trimmed = text.trim()
  if (!trimmed) return 'Clinical justification is required for break-glass access.'
  const minLength = reasonCode === 'OTHER_CLINICALLY_JUSTIFIED_EMERGENCY'
    ? OTHER_JUSTIFICATION_LENGTH
    : MIN_JUSTIFICATION_LENGTH
  if (trimmed.length < minLength) {
    return `Clinical justification must be at least ${minLength} characters (currently ${trimmed.length}). Provide specific detail about the clinical situation and why access is necessary.`
  }
  return null
}

// ── Mask token ──────────────────────────────────────────────────────────────

function maskToken(token: string): string {
  if (!token || token.length < 12) return '••••••••'
  return `${token.slice(0, 6)}••••${token.slice(-4)}`
}

interface BreakGlassResponse {
  consent_token: string
  expires_at: string
}

export function EmergencyAccessScreen() {
  const router = useRouter()
  const { providerId, hospitalName, isAuthenticated } = useProviderAuth()

  const [patientId, setPatientId] = useState('')
  const [reasonCode, setReasonCode] = useState<BreakGlassReason>('IMMEDIATE_THREAT_TO_LIFE')
  const [freeText, setFreeText] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<BreakGlassResponse | null>(null)

  const isOtherReason = reasonCode === 'OTHER_CLINICALLY_JUSTIFIED_EMERGENCY'
  const justificationError = validateJustification(freeText, reasonCode)
  const canSubmit = !!(patientId.trim() && freeText.trim() && !justificationError)

  // ── Session guard ─────────────────────────────────────────────────────
  if (!isAuthenticated) {
    return (
      <YStack flex={1} bg="$background" justifyContent="center" alignItems="center" gap="$4">
        <Text fontSize={44}>🔒</Text>
        <Text fontSize={22} fontWeight="900" color="$red10" textAlign="center">Session Required</Text>
        <Paragraph textAlign="center" color="$color11">You must be logged in for emergency access.</Paragraph>
        <Button theme="blue" size="$4" onPress={() => router.push('/doctor/login')}>Go to Login</Button>
      </YStack>
    )
  }

  const handleBreakGlass = async () => {
    if (!patientId.trim()) { setError('Patient ID is required for emergency access.'); return }
    const justError = validateJustification(freeText, reasonCode)
    if (justError) { setError(justError); return }
    setSubmitting(true)
    setError(null)
    try {
      const data = await NexaApiClient.breakGlassIssue({
        patient_id: patientId.trim(),
        reason_code: reasonCode,
        free_text: freeText.trim(),
      }) as BreakGlassResponse
      setResult(data)
    } catch {
      setError('Break-glass request failed. Contact system administrator.')
    } finally {
      setSubmitting(false)
    }
  }

  // ── Render: Success ──────────────────────────────────────────────────

  if (result) {
    return (
      <YStack flex={1} bg="$background" padding="$5" gap="$5" maxWidth={600} marginHorizontal="auto">
        <YStack gap="$2" alignItems="center">
          <AlertTriangle size={64} color="$red10" />
          <Text fontSize={24} fontWeight="900" color="$red10" textAlign="center">Emergency Access Granted</Text>
        </YStack>

        <Card backgroundColor="$red2" borderWidth={2} borderColor="$red8" padding="$4">
          <Text color="$red10" fontWeight="700">⚠️ This break-glass access is permanently recorded in the audit trail. This access will be recorded and may trigger patient and compliance notifications.</Text>
        </Card>

        {isOtherReason && (
          <Card backgroundColor="$orange2" borderWidth={1} borderColor="$orange8" padding="$4">
            <Text color="$orange10" fontWeight="700">⚠️ The "Other Clinically Justified Emergency" reason code triggers mandatory compliance review. Your justification will be evaluated by a compliance officer.</Text>
          </Card>
        )}

        <Card backgroundColor="$color2" borderWidth={1} borderColor="$borderColor" padding="$4" gap="$2">
          <Paragraph color="$color10" fontSize={12}>Patient</Paragraph>
          <Text color="$color12" fontWeight="700">{patientId}</Text>
          <Separator marginVertical="$2" />
          <Paragraph color="$color10" fontSize={12}>Reason</Paragraph>
          <Text color="$red10" fontWeight="600">{REASON_OPTIONS.find((r) => r.value === reasonCode)?.label ?? reasonCode}</Text>
          <Separator marginVertical="$2" />
          <Paragraph color="$color10" fontSize={12}>Justification</Paragraph>
          <Text color="$color12" fontSize={14}>{freeText}</Text>
          <Separator marginVertical="$2" />
          <Paragraph color="$color10" fontSize={12}>Expires At</Paragraph>
          <Text color="$color12" fontWeight="600">{result.expires_at}</Text>
          <Separator marginVertical="$2" />
          <Paragraph color="$color10" fontSize={12}>Authorization Reference</Paragraph>
          <Text color="$color12" fontWeight="600">{maskToken(result.consent_token)}</Text>
          <Separator marginVertical="$2" />
          <Paragraph color="$color10" fontSize={12}>Provider</Paragraph>
          <Text color="$color12" fontSize={14}>{providerId || 'Unknown'} · {hospitalName || 'Hospital'}</Text>
        </Card>

        <Paragraph color="$color10" textAlign="center" fontSize={12}>
          ALPHA: Emergency access currently grants full record access for 15 minutes.
          Production should limit break-glass scope to a minimum-safety dataset.
        </Paragraph>

        <Button theme="blue" size="$4" onPress={() => router.push(`/doctor/patient-record?request_id=${encodeURIComponent(result.consent_token)}&patient_id=${encodeURIComponent(patientId)}`)}>View Patient Record</Button>
        <Button size="$4" chromeless onPress={() => router.push('/doctor/dashboard')}>Back to Dashboard</Button>
      </YStack>
    )
  }

  // ── Render: Form ────────────────────────────────────────────────────

  return (
    <ScrollView>
      <YStack flex={1} bg="$background" padding="$5" gap="$5" maxWidth={600} marginHorizontal="auto">
        <YStack gap="$2">
          <XStack alignItems="center" gap="$2">
            <AlertTriangle size={36} color="$red10" />
            <Text fontSize={26} fontWeight="900" color="$red10">Emergency Access</Text>
          </XStack>
          <Paragraph color="$color11">Break-glass access bypasses patient consent for life-threatening situations. All accesses are permanently audited.</Paragraph>
        </YStack>

        <Card backgroundColor="$red2" borderWidth={1} borderColor="$red8" padding="$4">
          <Text color="$red10" fontWeight="600">⚠️ This action is audited. Use only in genuine emergencies. Unauthorized use is a compliance violation. Rate limit: 3 per hour (server-enforced). High-assurance session recommended (recent MFA).</Text>
        </Card>

        <YStack gap="$3">
          <YStack gap="$2">
            <Paragraph color="$color11">Patient ID</Paragraph>
            <Input size="$4" value={patientId} onChangeText={setPatientId} placeholder="Enter patient ID" />
          </YStack>

          <YStack gap="$2">
            <Paragraph color="$color11">Reason Code</Paragraph>
            <Select value={reasonCode} onValueChange={(v) => setReasonCode(v as BreakGlassReason)}>
              <Select.Trigger size="$4" iconAfter={ChevronDown}>
                <Select.Value placeholder="Select reason" />
              </Select.Trigger>
              <Select.Content>
                <Select.Viewport>
                  {REASON_OPTIONS.map((opt, index) => (
                    <Select.Item key={opt.value} index={index} value={opt.value}>
                      <Select.ItemText>{opt.label}</Select.ItemText>
                    </Select.Item>
                  ))}
                </Select.Viewport>
              </Select.Content>
            </Select>
            <Paragraph color="$color10" fontSize={13}>{REASON_OPTIONS.find((o) => o.value === reasonCode)?.description}</Paragraph>
          </YStack>

          <YStack gap="$2">
            <Paragraph color="$color11">Clinical Justification (required, min {isOtherReason ? OTHER_JUSTIFICATION_LENGTH : MIN_JUSTIFICATION_LENGTH} chars)</Paragraph>
            <Input size="$4" value={freeText} onChangeText={setFreeText}
              placeholder={isOtherReason
                ? 'Describe the clinical situation, why access is necessary, and expected harm if delayed...'
                : 'Describe the clinical situation and why access is necessary...'}
              multiline numberOfLines={3} />
            {freeText.trim().length > 0 && justificationError && (
              <Text color="$orange9" fontSize={12}>{justificationError}</Text>
            )}
            {isOtherReason && (
              <Paragraph color="$orange9" fontSize={12}>
                ⚠️ "Other" reason codes trigger mandatory compliance review.
              </Paragraph>
            )}
          </YStack>

          <Card backgroundColor="$color2" borderWidth={1} borderColor="$borderColor" padding="$3" gap="$1">
            <Text color="$color10" fontSize={12}>Provider: {providerId || 'Unknown'}</Text>
            <Text color="$color10" fontSize={12}>Hospital: {hospitalName || 'Unknown'}</Text>
          </Card>
        </YStack>

        {error && <Text color="$red10">{error}</Text>}

        <XStack gap="$3">
          <Button theme="red" size="$4" disabled={submitting || !canSubmit} onPress={handleBreakGlass}>
            {submitting ? <XStack gap="$2" alignItems="center"><Spinner size="small" color="$red10" /><Text color="$color12">Issuing...</Text></XStack> : 'Issue Break-Glass Access'}
          </Button>
          <Button size="$4" chromeless onPress={() => router.push('/doctor/dashboard')}>Cancel</Button>
        </XStack>
      </YStack>
    </ScrollView>
  )
}
