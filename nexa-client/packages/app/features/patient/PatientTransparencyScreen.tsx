'use client'

import { NexaApiClient } from '../../utils/apiClient'
import { Card, Text, YStack, ScrollView } from '@my/ui'
import { useState, useEffect } from 'react'

interface AccessLog {
  timestamp: string
  clinician_id: string
  hospital_id: string
  purpose: string
  consent_assurance: string
  action: string
}

interface Props {
  patientUuid: string
}

export function PatientTransparencyScreen({ patientUuid }: Props) {
  const [logs, setLogs] = useState<AccessLog[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const fetchAccessLog = async () => {
      try {
        const data = await NexaApiClient.getAccessLog(patientUuid)
        setLogs(data)
      } catch (e) {
        console.error('Failed to fetch access log', e)
      } finally {
        setLoading(false)
      }
    }
    fetchAccessLog()
  }, [patientUuid])

  return (
    <ScrollView
      flex={1}
      bg="$background"
      p="$5"
    >
      <YStack gap="$4">
        <Text
          fontSize={24}
          fontWeight="900"
          color="$color12"
        >
          Who Accessed My Data
        </Text>
        <Text color="$color11">Patient UUID: {patientUuid}</Text>

        {loading ? (
          <Text color="$color11">Loading access history...</Text>
        ) : (
          logs.map((log, index) => (
            <Card
              key={index}
              p="$4"
              bg="$color2"
              borderWidth={1}
              borderColor="$borderColor"
            >
              <YStack gap="$2">
                <Text
                  fontSize={15}
                  color="$color11"
                >
                  {log.timestamp}
                </Text>
                <Text
                  fontSize={17}
                  fontWeight="800"
                  color="$color12"
                >
                  {log.clinician} • {log.hospital}
                </Text>
                <Text color="$blue11">{log.purpose}</Text>
                <Text
                  fontSize={13}
                  color="$green10"
                >
                  Assurance: {log.assurance}
                </Text>
              </YStack>
            </Card>
          ))
        )}
      </YStack>
    </ScrollView>
  )
}
