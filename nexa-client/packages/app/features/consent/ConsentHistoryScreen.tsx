'use client'

import {
  Card,
  Text,
  YStack,
  XStack,
  ScrollView,
  Button,
} from '@my/ui'
import { Clock, UserCheck } from '@tamagui/lucide-icons'
import { useState, useEffect } from 'react'

interface ConsentRecord {
  id: string
  patient_id: string
  purpose: string
  issued_at: string
  expires_at: string
  type: string
}

export function ConsentHistoryScreen() {
  const [history, setHistory] = useState<ConsentRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState<'all' | 'routine' | 'break-glass'>('all')

  useEffect(() => {
    const fetchHistory = async () => {
      try {
        const res = await fetch('http://localhost:8000/api/v2/consent/history')
        const data = await res.json()
        setHistory(data)
      } catch (e) {
        console.error('Failed to fetch consent history', e)
      } finally {
        setLoading(false)
      }
    }
    fetchHistory()
  }, [])

  const filtered = history.filter((item) =>
    filter === 'all' ? true : item.type === filter
  )

  if (loading) {
    return (
      <YStack flex={1} items="center" justify="center" bg="$background">
        <Text color="$color11">Loading consent history...</Text>
      </YStack>
    )
  }

  return (
    <YStack flex={1} bg="$background" p="$5" gap="$5">
      <YStack gap="$2">
        <Text fontSize={26} fontWeight="900" color="$color12">
          Consent History
        </Text>
        <Text color="$color11">Audit trail of issued consent tokens</Text>
      </YStack>

      <XStack gap="$2">
        {(['all', 'routine', 'break-glass'] as const).map((f) => (
          <Button
            key={f}
            size="$3"
            theme={filter === f ? 'blue' : undefined}
            onPress={() => setFilter(f)}
          >
            {f === 'all' ? 'All' : f === 'routine' ? 'Routine' : 'Break-Glass'}
          </Button>
        ))}
      </XStack>

      <ScrollView flex={1}>
        <YStack gap="$3">
          {filtered.length === 0 ? (
            <Text color="$color11" text="center" pt="$8">
              No consent records found.
            </Text>
          ) : (
            filtered.map((record) => (
              <Card
                key={record.id}
                p="$4"
                bg="$color2"
                borderWidth={1}
                borderColor="$borderColor"
              >
                <YStack gap="$3">
                  <XStack justify="space-between" items="center">
                    <XStack gap="$2" items="center">
                      <UserCheck size={18} color="$blue10" />
                      <Text fontWeight="800" color="$color12">
                        {record.patient_id}
                      </Text>
                    </XStack>
                    <Text 
                      fontSize={12} 
                      color={record.type === 'break-glass' ? '$red10' : '$green10'}
                      fontWeight="700"
                    >
                      {record.type.toUpperCase()}
                    </Text>
                  </XStack>

                  <Text color="$color11" fontSize={15}>
                    Purpose: <Text color="$color12" fontWeight="700">{record.purpose}</Text>
                  </Text>

                  <XStack gap="$4" flexWrap="wrap">
                    <XStack gap="$1.5" items="center">
                      <Clock size={15} color="$color10" />
                      <Text fontSize={13} color="$color11">
                        Issued: {record.issued_at}
                      </Text>
                    </XStack>
                    <Text fontSize={13} color="$color11">
                      Expires: {record.expires_at}
                    </Text>
                  </XStack>
                </YStack>
              </Card>
            ))
          )}
        </YStack>
      </ScrollView>
    </YStack>
  )
}
