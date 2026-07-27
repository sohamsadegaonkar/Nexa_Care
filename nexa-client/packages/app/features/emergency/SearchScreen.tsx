'use client'

import {
  Button,
  Card,
  Input,
  ScrollView,
  Select,
  Sheet,
  Spinner,
  Text,
  TextArea,
  XStack,
  YStack,
} from '@my/ui'
import { Check, ChevronDown } from '@tamagui/lucide-icons'
import { useRouter } from 'solito/navigation'
import { useState } from 'react'

import {
  BREAK_GLASS_REASON_OPTIONS,
  requestBreakGlassConsent,
  BreakGlassConsentError,
  type BreakGlassReasonCode,
} from '../../api/consent'
import { generateWorkflowId, setCapability } from '../../services/capabilityStore'

const BREAK_GLASS_PURPOSE = 'EMERGENCY'
const DEFAULT_REASON_CODE: BreakGlassReasonCode = 'UNCONSCIOUS_PATIENT'
const REASON_CODES = BREAK_GLASS_REASON_OPTIONS

export interface EmergencyPatientSearchQuery {
  firstName: string
  lastName: string
  dob: string
}

export interface EmergencyPatientSearchResult {
  patient_id: string
  display_name: string
  dob: string
}

export async function searchPatients(
  query: EmergencyPatientSearchQuery
): Promise<EmergencyPatientSearchResult[]> {
  const firstName = query.firstName.trim()
  const lastName = query.lastName.trim()
  const dob = query.dob.trim()

  if (!firstName || !lastName || !dob) {
    return []
  }

  await Promise.resolve()

  return [
    {
      patient_id: '00000000-0000-4000-8000-000000000001',
      display_name: `${firstName} ${lastName}`,
      dob,
    },
  ]
}

function isBreakGlassReasonCode(value: string): value is BreakGlassReasonCode {
  return BREAK_GLASS_REASON_OPTIONS.some((reason) => reason.value === value)
}

function reasonLabel(value: BreakGlassReasonCode): string {
  return REASON_CODES.find((reason) => reason.value === value)?.label ?? value
}

interface BreakGlassSheetProps {
  patient: EmergencyPatientSearchResult | null
  open: boolean
  reasonCode: BreakGlassReasonCode
  freeText: string
  loading: boolean
  errorMessage: string | null
  onOpenChange: (open: boolean) => void
  onReasonCodeChange: (reasonCode: BreakGlassReasonCode) => void
  onFreeTextChange: (freeText: string) => void
  onSubmit: () => void
}

function BreakGlassSheet({
  patient,
  open,
  reasonCode,
  freeText,
  loading,
  errorMessage,
  onOpenChange,
  onReasonCodeChange,
  onFreeTextChange,
  onSubmit,
}: BreakGlassSheetProps) {
  return (
    <Sheet
      modal
      open={open}
      onOpenChange={onOpenChange}
      snapPoints={[82]}
      dismissOnSnapToBottom={!loading}
    >
      <Sheet.Overlay
        bg="$shadow8"
        opacity={0.86}
      />
      <Sheet.Handle bg="$red9" />
      <Sheet.Frame
        bg="$color1"
        borderTopWidth={4}
        borderColor="$red10"
        p="$5"
        gap="$5"
      >
        <YStack gap="$2">
          <Text
            color="$red11"
            fontSize={24}
            fontWeight="900"
          >
            ⚠️ INITIATE EMERGENCY OVERRIDE
          </Text>
          <Text
            color="$color12"
            fontSize={16}
            fontWeight="900"
          >
            WARNING: This action bypasses standard consent and will trigger an immediate compliance
            audit.
          </Text>
        </YStack>

        {patient && (
          <Card
            width="100%"
            borderWidth={2}
            borderColor="$red9"
            bg="$red2"
            p="$4"
          >
            <YStack gap="$1">
              <Text
                color="$red11"
                fontSize={14}
                fontWeight="900"
              >
                Selected Patient
              </Text>
              <Text
                color="$color12"
                fontSize={18}
                fontWeight="900"
              >
                {patient.display_name}
              </Text>
              <Text
                color="$color11"
                fontSize={14}
                fontWeight="800"
              >
                {patient.patient_id}
              </Text>
            </YStack>
          </Card>
        )}

        <YStack gap="$3">
          <Text
            color="$color12"
            fontSize={15}
            fontWeight="900"
          >
            Reason Code
          </Text>
          <Select
            value={reasonCode}
            onValueChange={(value) => {
              if (isBreakGlassReasonCode(value)) {
                onReasonCodeChange(value)
              }
            }}
            disablePreventBodyScroll
          >
            <Select.Trigger
              width="100%"
              size="$5"
              iconAfter={ChevronDown}
              borderColor="$red9"
            >
              <Select.Value placeholder="Select reason">{reasonLabel(reasonCode)}</Select.Value>
            </Select.Trigger>
            <Select.Content zIndex={200000}>
              <Select.Viewport>
                <Select.Group>
                  {REASON_CODES.map((reason, index) => (
                    <Select.Item
                      key={reason.value}
                      index={index}
                      value={reason.value}
                    >
                      <Select.ItemText>{reason.label}</Select.ItemText>
                      <Select.ItemIndicator marginLeft="auto">
                        <Check size={16} />
                      </Select.ItemIndicator>
                    </Select.Item>
                  ))}
                </Select.Group>
              </Select.Viewport>
            </Select.Content>
          </Select>
        </YStack>

        <YStack gap="$3">
          <Text
            color="$color12"
            fontSize={15}
            fontWeight="900"
          >
            Mandatory Justification
          </Text>
          <TextArea
            minH={120}
            value={freeText}
            borderColor="$red9"
            disabled={loading}
            placeholder="Document the clinical emergency and why standard consent cannot be obtained."
            onChangeText={onFreeTextChange}
          />
        </YStack>

        {errorMessage && (
          <Text
            color="$red11"
            fontSize={16}
            fontWeight="900"
          >
            {errorMessage}
          </Text>
        )}

        <Button
          size="$5"
          theme="red"
          borderWidth={2}
          borderColor="$red11"
          disabled={loading || !patient || !reasonCode || freeText.trim().length === 0}
          onPress={onSubmit}
        >
          {loading ? (
            <XStack
              gap="$2"
              items="center"
            >
              <Spinner color="$color12" />
              <Text
                color="$color12"
                fontWeight="900"
              >
                Creating Override...
              </Text>
            </XStack>
          ) : (
            'Acknowledge & Access Record'
          )}
        </Button>
      </Sheet.Frame>
    </Sheet>
  )
}

export function SearchScreen() {
  const router = useRouter()
  const [firstName, setFirstName] = useState('')
  const [lastName, setLastName] = useState('')
  const [dob, setDob] = useState('')
  const [results, setResults] = useState<EmergencyPatientSearchResult[]>([])
  const [selectedPatient, setSelectedPatient] = useState<EmergencyPatientSearchResult | null>(null)
  const [reasonCode, setReasonCode] = useState<BreakGlassReasonCode>(DEFAULT_REASON_CODE)
  const [freeText, setFreeText] = useState('')
  const [isSearching, setIsSearching] = useState(false)
  const [isRequestingOverride, setIsRequestingOverride] = useState(false)
  const [searchErrorMessage, setSearchErrorMessage] = useState<string | null>(null)
  const [overrideErrorMessage, setOverrideErrorMessage] = useState<string | null>(null)

  const handleSearch = async (): Promise<void> => {
    setIsSearching(true)
    setSearchErrorMessage(null)

    try {
      const matches = await searchPatients({ firstName, lastName, dob })
      setResults(matches)
      if (matches.length === 0) {
        setSearchErrorMessage('No matching patients found.')
      }
    } catch (_error: unknown) {
      setResults([])
      setSearchErrorMessage('Patient search is temporarily unavailable.')
    } finally {
      setIsSearching(false)
    }
  }

  const handleSelectPatient = (patient: EmergencyPatientSearchResult): void => {
    setSelectedPatient(patient)
    setReasonCode(DEFAULT_REASON_CODE)
    setFreeText('')
    setOverrideErrorMessage(null)
  }

  const handleBreakGlassAccess = async (): Promise<void> => {
    if (!selectedPatient) {
      return
    }

    setIsRequestingOverride(true)
    setOverrideErrorMessage(null)

    try {
      const grant = await requestBreakGlassConsent(selectedPatient.patient_id, reasonCode, freeText)
      const workflowId = generateWorkflowId()
      setCapability({
        workflowId,
        patientId: selectedPatient.patient_id,
        token: grant.consent_token,
        purpose: BREAK_GLASS_PURPOSE,
        scope: grant.approved_scope,
        expiresAt: grant.expires_at ?? new Date(Date.now() + 15 * 60 * 1000).toISOString(),
      })
      router.push(
        `/patient/${encodeURIComponent(selectedPatient.patient_id)}?workflow_id=${encodeURIComponent(
          workflowId
        )}`
      )
    } catch (error: unknown) {
      if (error instanceof BreakGlassConsentError) {
        setOverrideErrorMessage(error.message)
      } else {
        setOverrideErrorMessage('Unable to initiate emergency override.')
      }
    } finally {
      setIsRequestingOverride(false)
    }
  }

  return (
    <ScrollView
      flex={1}
      bg="$background"
      contentContainerStyle={{ minHeight: '100%' }}
    >
      <YStack
        width="100%"
        maxW={760}
        mx="auto"
        p="$5"
        gap="$5"
      >
        <YStack gap="$2">
          <Text
            color="$red11"
            fontSize={28}
            fontWeight="900"
          >
            Emergency Break-Glass
          </Text>
          <Text
            color="$color12"
            fontSize={16}
            fontWeight="800"
          >
            Search patient identity before initiating emergency override.
          </Text>
        </YStack>

        <Card
          width="100%"
          borderWidth={2}
          borderColor="$red8"
          bg="$color2"
          p="$5"
        >
          <YStack gap="$4">
            <XStack
              gap="$3"
              flexWrap="wrap"
            >
              <Input
                flex={1}
                minW={180}
                size="$5"
                value={firstName}
                placeholder="First name"
                autoCapitalize="words"
                onChangeText={setFirstName}
              />
              <Input
                flex={1}
                minW={180}
                size="$5"
                value={lastName}
                placeholder="Last name"
                autoCapitalize="words"
                onChangeText={setLastName}
              />
              <Input
                flex={1}
                minW={160}
                size="$5"
                value={dob}
                placeholder="DOB YYYY-MM-DD"
                onChangeText={setDob}
              />
            </XStack>
            <Button
              size="$5"
              theme="red"
              disabled={isSearching}
              onPress={() => {
                void handleSearch()
              }}
            >
              {isSearching ? (
                <XStack
                  gap="$2"
                  items="center"
                >
                  <Spinner color="$color12" />
                  <Text
                    color="$color12"
                    fontWeight="900"
                  >
                    Searching...
                  </Text>
                </XStack>
              ) : (
                'Search Emergency Patient'
              )}
            </Button>
          </YStack>
        </Card>

        {searchErrorMessage && (
          <Text
            color="$red11"
            fontSize={16}
            fontWeight="900"
          >
            {searchErrorMessage}
          </Text>
        )}

        <YStack gap="$3">
          {results.map((patient) => (
            <Card
              key={patient.patient_id}
              width="100%"
              borderWidth={2}
              borderColor="$color7"
              bg="$color2"
              p="$4"
            >
              <XStack
                justify="space-between"
                items="center"
                gap="$4"
                flexWrap="wrap"
              >
                <YStack gap="$1">
                  <Text
                    color="$color12"
                    fontSize={18}
                    fontWeight="900"
                  >
                    {patient.display_name}
                  </Text>
                  <Text
                    color="$color11"
                    fontSize={14}
                    fontWeight="800"
                  >
                    DOB {patient.dob}
                  </Text>
                  <Text
                    color="$color11"
                    fontSize={13}
                    fontWeight="700"
                  >
                    {patient.patient_id}
                  </Text>
                </YStack>
                <Button
                  size="$4"
                  theme="red"
                  onPress={() => handleSelectPatient(patient)}
                >
                  Emergency Access
                </Button>
              </XStack>
            </Card>
          ))}
        </YStack>
      </YStack>

      <BreakGlassSheet
        patient={selectedPatient}
        open={selectedPatient !== null}
        reasonCode={reasonCode}
        freeText={freeText}
        loading={isRequestingOverride}
        errorMessage={overrideErrorMessage}
        onOpenChange={(open) => {
          if (!open && !isRequestingOverride) {
            setSelectedPatient(null)
          }
        }}
        onReasonCodeChange={setReasonCode}
        onFreeTextChange={setFreeText}
        onSubmit={() => {
          void handleBreakGlassAccess()
        }}
      />
    </ScrollView>
  )
}
