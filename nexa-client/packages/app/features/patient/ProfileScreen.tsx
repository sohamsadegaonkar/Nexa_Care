'use client'

import { Button, Card, ScrollView, Spinner, Text, XStack, YStack } from '@my/ui'
import { useCallback, useEffect, useState } from 'react'

import {
  fetchPatientRecord,
  PatientRecordError,
  type PatientClinicalData,
  type PatientDemographics,
  type PatientRecordResponse,
} from '../../api/patient'

interface ProfileScreenProps {
  patientId: string
  consentToken?: string | null
  purpose?: string | null
}

type LoadState = 'idle' | 'loading' | 'success' | 'error'

function asList(value: string[] | string | undefined): string[] {
  if (Array.isArray(value)) {
    return value.filter((item) => item.trim().length > 0)
  }

  if (typeof value === 'string' && value.trim().length > 0) {
    return [value.trim()]
  }

  return []
}

function getDemographics(record: PatientRecordResponse): PatientDemographics {
  return record.demographics ?? record.pii ?? {}
}

function getClinical(record: PatientRecordResponse): PatientClinicalData {
  return record.clinical ?? {}
}

function getDisplayValue(value: string | number | undefined, fallback = 'Not provided'): string {
  if (value === undefined || value === null || String(value).trim().length === 0) {
    return fallback
  }

  return String(value)
}

function DataRow({ label, value }: { label: string; value: string }) {
  return (
    <XStack
      width="100%"
      justify="space-between"
      items="flex-start"
      gap="$4"
    >
      <Text
        color="$color11"
        fontSize={15}
        fontWeight="700"
      >
        {label}
      </Text>
      <Text
        color="$color12"
        fontSize={16}
        fontWeight="800"
        maxW="60%"
        text="right"
      >
        {value}
      </Text>
    </XStack>
  )
}

function ClinicalBlock({ title, items }: { title: string; items: string[] }) {
  return (
    <Card
      width="100%"
      borderWidth={2}
      borderColor="$blue8"
      bg="$color2"
      p="$4"
    >
      <YStack gap="$3">
        <Text
          color="$blue11"
          fontSize={16}
          fontWeight="900"
        >
          {title}
        </Text>
        {items.length > 0 ? (
          <YStack gap="$2">
            {items.map((item, index) => (
              <Text
                key={`${title}-${index}-${item}`}
                color="$color12"
                fontSize={16}
                fontWeight="700"
              >
                {item}
              </Text>
            ))}
          </YStack>
        ) : (
          <Text
            color="$color11"
            fontSize={15}
            fontWeight="700"
          >
            No scoped data returned
          </Text>
        )}
      </YStack>
    </Card>
  )
}

function LoadingState() {
  return (
    <YStack
      flex={1}
      minH="100%"
      bg="$background"
      items="center"
      justify="center"
      gap="$4"
      p="$5"
    >
      <Spinner
        size="large"
        color="$blue11"
      />
      <Text
        color="$color12"
        fontSize={18}
        fontWeight="800"
        text="center"
      >
        Loading consent-scoped record...
      </Text>
    </YStack>
  )
}

function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <YStack
      flex={1}
      minH="100%"
      bg="$background"
      items="center"
      justify="center"
      gap="$4"
      p="$5"
    >
      <Text
        color="$red11"
        fontSize={20}
        fontWeight="900"
        text="center"
      >
        {message}
      </Text>
      <Button
        size="$5"
        theme="blue"
        onPress={onRetry}
      >
        Retry
      </Button>
    </YStack>
  )
}

export function ProfileScreen({ patientId, consentToken, purpose }: ProfileScreenProps) {
  const [state, setState] = useState<LoadState>('idle')
  const [record, setRecord] = useState<PatientRecordResponse | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const loadRecord = useCallback(async (): Promise<void> => {
    setState('loading')
    setErrorMessage(null)
    setRecord(null)

    try {
      const response = await fetchPatientRecord(patientId, consentToken ?? '', purpose ?? '')
      setRecord(response)
      setState('success')
    } catch (error: unknown) {
      if (error instanceof PatientRecordError) {
        setErrorMessage(error.message)
      } else {
        setErrorMessage('Unable to fetch patient record.')
      }
      setState('error')
    }
  }, [patientId, consentToken, purpose])

  useEffect(() => {
    void loadRecord()
  }, [loadRecord])

  if (state === 'loading' || state === 'idle') {
    return <LoadingState />
  }

  if (state === 'error' || !record) {
    return <ErrorState message={errorMessage ?? 'Unable to fetch patient record.'} onRetry={loadRecord} />
  }

  const demographics = getDemographics(record)
  const clinical = getClinical(record)
  const patientName = demographics.name ?? demographics.patient_name
  const bloodType = demographics.bloodType ?? demographics.blood_type
  const contactInfo =
    demographics.contactInfo ?? demographics.contact_info ?? demographics.phone
  const medications = asList(clinical.medications ?? clinical.prescriptions ?? record.medications)
  const allergies = asList(clinical.allergies ?? record.allergies)
  const recentDiagnoses = asList(clinical.recentDiagnoses ?? clinical.recent_diagnoses ?? clinical.diagnoses)

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
            color="$color12"
            fontSize={28}
            fontWeight="900"
          >
            Patient Profile
          </Text>
          <Text
            color="$color11"
            fontSize={15}
            fontWeight="700"
          >
            Consent-scoped reconstructed record
          </Text>
        </YStack>

        <Card
          width="100%"
          borderWidth={2}
          borderColor="$green8"
          bg="$color2"
          p="$5"
        >
          <YStack gap="$4">
            <Text
              color="$green11"
              fontSize={18}
              fontWeight="900"
            >
              Demographics
            </Text>
            <DataRow label="Name" value={getDisplayValue(patientName)} />
            <DataRow label="Age" value={getDisplayValue(demographics.age)} />
            <DataRow label="Blood Type" value={getDisplayValue(bloodType)} />
            <DataRow label="Contact Info" value={getDisplayValue(contactInfo)} />
          </YStack>
        </Card>

        <YStack gap="$3">
          <Text
            color="$color12"
            fontSize={20}
            fontWeight="900"
          >
            Clinical Data
          </Text>
          <ClinicalBlock title="Medications" items={medications} />
          <ClinicalBlock title="Allergies" items={allergies} />
          <ClinicalBlock title="Recent Diagnoses" items={recentDiagnoses} />
        </YStack>
      </YStack>
    </ScrollView>
  )
}
