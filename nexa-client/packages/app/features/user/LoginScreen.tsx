'use client'

import { NexaApiClient } from '../../utils/apiClient'
import {
  Button,
  Input,
  Text,
  YStack,
  XStack,
  Card,
  Spinner,
} from '@my/ui'
import { useState } from 'react'

interface LoginScreenProps {
  onLoginSuccess?: (providerId: string, token: string) => void
}

export function LoginScreen({ onLoginSuccess }: LoginScreenProps) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showMfa, setShowMfa] = useState(false)
  const [mfaToken, setMfaToken] = useState('')
  const [mfaCode, setMfaCode] = useState('')

  const handleLogin = async () => {
    if (!email || !password) {
      setError('Email and password are required')
      return
    }

    setLoading(true)
    setError(null)

    try {
      const data = await NexaApiClient.login({ email, password })

      if (data.mfa_token) {
        setMfaToken(data.mfa_token)
        setShowMfa(true)
      } else {
        onLoginSuccess?.(data.provider_id, data.access_token)
      }
    } catch (err: any) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const handleMfaVerify = async () => {
    if (!mfaCode) return

    setLoading(true)
    setError(null)

    try {
      const data = await NexaApiClient.verifyMfa({ mfa_token: mfaToken, code: mfaCode })
      onLoginSuccess?.(data.provider_id, data.access_token)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <YStack flex={1} bg="$background" justify="center" items="center" p="$5">
      <Card 
        width="100%" 
        maxW={420} 
        p="$6" 
        gap="$5" 
        bg="$color2" 
        borderWidth={1} 
        borderColor="$borderColor"
      >
        <YStack gap="$2">
          <Text fontSize={28} fontWeight="900" color="$color12" text="center">
            Nexa Care
          </Text>
          <Text color="$color11" text="center" fontSize={16}>
            Provider Login
          </Text>
        </YStack>

        {!showMfa ? (
          <YStack gap="$4">
            <Input
              placeholder="Email"
              value={email}
              onChangeText={setEmail}
              autoCapitalize="none"
              keyboardType="email-address"
              size="$5"
            />
            <Input
              placeholder="Password"
              value={password}
              onChangeText={setPassword}
              secureTextEntry
              size="$5"
            />

            {error && (
              <Text color="$red11" fontSize={14} text="center">
                {error}
              </Text>
            )}

            <Button
              theme="blue"
              size="$5"
              onPress={handleLogin}
              disabled={loading}
            >
              {loading ? <Spinner color="$color12" /> : 'Sign In'}
            </Button>
          </YStack>
        ) : (
          <YStack gap="$4">
            <Text color="$color11" text="center" fontSize={15}>
              Enter your 6-digit MFA code
            </Text>
            <Input
              placeholder="123456"
              value={mfaCode}
              onChangeText={setMfaCode}
              keyboardType="numeric"
              maxLength={6}
              size="$5"
              text="center"
            />
            {error && (
              <Text color="$red11" fontSize={14} text="center">
                {error}
              </Text>
            )}
            <Button 
              theme="blue" 
              size="$5" 
              onPress={handleMfaVerify} 
              disabled={loading}
            >
              {loading ? <Spinner color="$color12" /> : 'Verify MFA'}
            </Button>
          </YStack>
        )}
      </Card>
    </YStack>
  )
}
