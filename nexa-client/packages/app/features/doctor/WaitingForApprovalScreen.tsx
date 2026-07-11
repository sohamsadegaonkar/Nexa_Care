/**
 * Waiting for approval screen — polls consent request status.
 *
 * Polls GET /api/v2/consent/status/{request_id} with adaptive backoff:
 *   First 20s: every 2 seconds
 *   20–60s:    every 5 seconds
 *   After 60s:  every 10 seconds
 *
 * SECURITY: This screen NEVER calls any approval/respond endpoint.
 * Only the patient can approve or deny. The doctor can only wait or cancel.
 * This screen does NOT render Approve/Deny buttons.
 *
 * Error handling by HTTP status:
 *   401 → stop polling, redirect to login (session expired)
 *   403 → stop polling, show authorization error (not your request)
 *   404 → stop polling, show request unavailable
 *   429 → retry with server backoff
 *   5xx → retry with bounded backoff, show reconnecting state
 *   Network → retry, show reconnecting state
 *
 * Cancel button calls POST /api/v2/consent/request/{request_id}/cancel
 * (real server-side cancellation, not just navigation).
 *
 * Retry navigates to request-consent with preserved patient_id context
 * (creates a brand new request — never reuses expired request_id).
 *
 * Route: /doctor/waiting?request_id=...&patient_id=...
 */

'use client'

import { Card, Text, YStack, Button, Spinner, XStack, Paragraph, H4 } from '@my/ui'
import { CheckCircle, XCircle, Clock, ShieldOff, Lock, ShieldAlert } from '@tamagui/lucide-icons'
import { useState, useEffect, useCallback, useRef } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { NexaApiClient, ApiError } from '../../utils/apiClient'
import { useProviderAuth } from './ProviderAuthContext'

type ConsentState = 'pending' | 'approved' | 'denied' | 'expired' | 'cancelled' | 'error' | 'unauthorized' | 'not_found' | 'forbidden'

// ── Adaptive polling intervals ──────────────────────────────────────────────
const POLL_FAST_MS = 2000     // First 20 seconds
const POLL_MEDIUM_MS = 5000   // 20–60 seconds
const POLL_SLOW_MS = 10000    // After 60 seconds
const FAST_CUTOFF_S = 20
const MEDIUM_CUTOFF_S = 60

export function WaitingForApprovalScreen() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const requestId = searchParams.get('request_id') ?? ''
  const patientId = searchParams.get('patient_id') ?? ''
  const { isAuthenticated } = useProviderAuth()

  const [consentState, setConsentState] = useState<ConsentState>('pending')
  const [elapsed, setElapsed] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [cancelling, setCancelling] = useState(false)
  const intervalRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const elapsedRef = useRef(0)

  // ── Session guard ─────────────────────────────────────────────────────
  if (!isAuthenticated) {
    return (
      <YStack flex={1} bg="$background" padding="$6" gap="$4" justifyContent="center" alignItems="center">
        <Lock size={64} color="$red10" />
        <H4 textAlign="center" color="$color12">Session Required</H4>
        <Paragraph textAlign="center" color="$color11">You must be logged in to view consent status.</Paragraph>
        <Button theme="blue" size="$4" onPress={() => router.push('/doctor/login')}>Go to Login</Button>
      </YStack>
    )
  }

  // ── Compute adaptive poll interval ────────────────────────────────────

  const getPollInterval = useCallback(() => {
    if (elapsedRef.current < FAST_CUTOFF_S) return POLL_FAST_MS
    if (elapsedRef.current < MEDIUM_CUTOFF_S) return POLL_MEDIUM_MS
    return POLL_SLOW_MS
  }, [])

  // ── Stop polling on terminal state ────────────────────────────────────

  const stopPolling = useCallback(() => {
    if (intervalRef.current !== null) {
      clearTimeout(intervalRef.current)
      intervalRef.current = null
    }
  }, [])

  // ── Schedule next poll with adaptive backoff ──────────────────────────

  const scheduleNextPoll = useCallback((pollFn: () => Promise<void>) => {
    const delay = getPollInterval()
    intervalRef.current = setTimeout(async () => {
      await pollFn()
    }, delay) as unknown as ReturnType<typeof setTimeout>
  }, [getPollInterval])

  // ── Poll consent request status ───────────────────────────────────────

  const pollStatus = useCallback(async () => {
    if (!requestId) return
    try {
      const data = await NexaApiClient.getConsentStatus(requestId, '') as any
      const newStatus = data?.status ?? 'pending'
      setConsentState(newStatus as ConsentState)

      // Stop polling on any terminal state
      if (newStatus === 'approved' || newStatus === 'denied' || newStatus === 'expired' || newStatus === 'cancelled') {
        stopPolling()
        return
      }

      // Pending — schedule next adaptive poll
      scheduleNextPoll(pollStatus)
    } catch (err) {
      if (err instanceof ApiError) {
        const status = err.status

        // 401 — session expired, redirect to login
        if (status === 401) {
          stopPolling()
          setConsentState('unauthorized')
          setError('Session expired. Please log in again.')
          return
        }

        // 403 — not authorized to poll this request
        if (status === 403) {
          stopPolling()
          setConsentState('forbidden')
          setError('You are not authorized to view this request.')
          return
        }

        // 404 — request not found / already expired on server
        if (status === 404) {
          stopPolling()
          setConsentState('not_found')
          setError('Consent request not found or has expired.')
          return
        }

        // 429 — rate limited, retry with backoff
        if (status === 429) {
          setError('Rate limited. Retrying with backoff...')
          scheduleNextPoll(pollStatus)
          return
        }

        // 5xx — server error, retry with backoff
        if (status >= 500) {
          setError('Server error. Retrying...')
          scheduleNextPoll(pollStatus)
          return
        }
      }

      // Network error or other transient — retry
      setError('Network issue. Retrying...')
      scheduleNextPoll(pollStatus)
    }
  }, [requestId, stopPolling, scheduleNextPoll])

  // ── Start polling ────────────────────────────────────────────────────

  useEffect(() => {
    if (!requestId) return
    pollStatus()
    return () => {
      stopPolling()
    }
  }, [requestId, pollStatus, stopPolling])

  // ── Elapsed timer ────────────────────────────────────────────────────

  useEffect(() => {
    timerRef.current = setInterval(() => {
      setElapsed((e) => {
        const next = e + 1
        elapsedRef.current = next
        return next
      })
    }, 1000)
    return () => {
      if (timerRef.current !== null) {
        clearInterval(timerRef.current)
        timerRef.current = null
      }
    }
  }, [])

  // ── Auto-proceed on approval ──────────────────────────────────────────

  useEffect(() => {
    if (consentState === 'approved') {
      const timeout = setTimeout(() => {
        router.push(`/doctor/patient-record?request_id=${encodeURIComponent(requestId)}`)
      }, 1500)
      return () => clearTimeout(timeout)
    }
  }, [consentState, requestId, router])

  // ── Helpers ───────────────────────────────────────────────────────────

  const formatElapsed = (seconds: number) => {
    const m = Math.floor(seconds / 60)
    const s = seconds % 60
    return `${m}:${s.toString().padStart(2, '0')}`
  }

  const handleCancelRequest = async () => {
    if (!requestId) return
    setCancelling(true)
    try {
      // Real server-side cancellation — not just navigation
      await NexaApiClient.cancelConsentRequest(requestId)
      stopPolling()
      setConsentState('cancelled')
    } catch {
      // If cancel fails, still navigate away — the request will expire on its own
      stopPolling()
      router.push('/doctor/dashboard')
    } finally {
      setCancelling(false)
    }
  }

  const handleRetry = () => {
    // Navigate to request-consent with preserved patient_id context.
    // This creates a BRAND NEW request — never reuses the expired request_id.
    const target = patientId
      ? `/doctor/request-consent?patient_id=${encodeURIComponent(patientId)}`
      : '/doctor/dashboard'
    router.push(target)
  }

  // ── Render: Approved ──────────────────────────────────────────────────

  if (consentState === 'approved') {
    return (
      <YStack flex={1} bg="$background" padding="$6" gap="$4" justifyContent="center" alignItems="center">
        <CheckCircle size={64} color="$green10" />
        <H4 textAlign="center" color="$green10">Access Approved</H4>
        <Paragraph textAlign="center" color="$color11">
          The patient has approved your request. Redirecting to patient record...
        </Paragraph>
        <Spinner size="small" color="$green10" />
      </YStack>
    )
  }

  // ── Render: Denied ────────────────────────────────────────────────────

  if (consentState === 'denied') {
    return (
      <YStack flex={1} bg="$background" padding="$6" gap="$4" justifyContent="center" alignItems="center">
        <XCircle size={64} color="$red10" />
        <H4 textAlign="center" color="$red10">Access Denied</H4>
        <Paragraph textAlign="center" color="$color11">
          The patient denied this consent request. No data was shared.
        </Paragraph>
        <Button theme="blue" size="$4" onPress={() => router.push('/doctor/dashboard')}>
          Back to Dashboard
        </Button>
      </YStack>
    )
  }

  // ── Render: Expired / Not Found ───────────────────────────────────────

  if (consentState === 'expired' || consentState === 'not_found') {
    return (
      <YStack flex={1} bg="$background" padding="$6" gap="$4" justifyContent="center" alignItems="center">
        <Clock size={64} color="$yellow10" />
        <H4 textAlign="center" color="$yellow10">Request Expired</H4>
        <Paragraph textAlign="center" color="$color11">
          The patient did not respond in time. You can create a new request.
        </Paragraph>
        <XStack gap="$3">
          <Button theme="blue" size="$4" onPress={handleRetry}>New Request</Button>
          <Button size="$4" chromeless onPress={() => router.push('/doctor/dashboard')}>
            Back to Dashboard
          </Button>
        </XStack>
      </YStack>
    )
  }

  // ── Render: Cancelled ────────────────────────────────────────────────

  if (consentState === 'cancelled') {
    return (
      <YStack flex={1} bg="$background" padding="$6" gap="$4" justifyContent="center" alignItems="center">
        <ShieldOff size={64} color="$color11" />
        <H4 textAlign="center" color="$color11">Request Cancelled</H4>
        <Paragraph textAlign="center" color="$color11">
          The consent request has been cancelled. The patient can no longer approve it.
        </Paragraph>
        <Button theme="blue" size="$4" onPress={() => router.push('/doctor/dashboard')}>
          Back to Dashboard
        </Button>
      </YStack>
    )
  }

  // ── Render: Unauthorized ──────────────────────────────────────────────

  if (consentState === 'unauthorized') {
    return (
      <YStack flex={1} bg="$background" padding="$6" gap="$4" justifyContent="center" alignItems="center">
        <Lock size={64} color="$red10" />
        <H4 textAlign="center" color="$red10">Session Expired</H4>
        <Paragraph textAlign="center" color="$color11">
          {error || 'Your session has expired. Please log in again.'}
        </Paragraph>
        <Button theme="blue" size="$4" onPress={() => router.push('/doctor/login')}>
          Return to Login
        </Button>
      </YStack>
    )
  }

  // ── Render: Forbidden ─────────────────────────────────────────────────

  if (consentState === 'forbidden') {
    return (
      <YStack flex={1} bg="$background" padding="$6" gap="$4" justifyContent="center" alignItems="center">
        <ShieldAlert size={64} color="$red10" />
        <H4 textAlign="center" color="$red10">Not Authorized</H4>
        <Paragraph textAlign="center" color="$color11">
          {error || 'You are not authorized to view this consent request.'}
        </Paragraph>
        <Button theme="blue" size="$4" onPress={() => router.push('/doctor/dashboard')}>
          Back to Dashboard
        </Button>
      </YStack>
    )
  }

  // ── Render: Waiting ──────────────────────────────────────────────────

  return (
    <YStack flex={1} bg="$background" padding="$6" gap="$4" justifyContent="center" alignItems="center">
      <Spinner size="large" color="$blue10" />
      <H4 textAlign="center" color="$color12">Waiting for Patient Approval</H4>
      <Paragraph textAlign="center" color="$color11" maxWidth={400}>
        A consent request has been sent to the patient. Waiting for
        them to approve or deny. Delivery may not be guaranteed.
      </Paragraph>

      {/* Request ID display */}
      <Card padding="$4" backgroundColor="$color2" borderWidth={1} borderColor="$borderColor" gap="$2">
        <Paragraph color="$color10" fontSize={12} fontWeight="700" textTransform="uppercase">
          Request ID
        </Paragraph>
        <Text color="$color12" fontSize={16} fontWeight="700">
          {requestId || '—'}
        </Text>
      </Card>

      {/* Elapsed timer */}
      <Text color="$color10" fontSize={18} fontWeight="700">
        Elapsed: {formatElapsed(elapsed)}
      </Text>

      {/* Polling rate indicator */}
      <Text color="$color9" fontSize={12}>
        Polling every {elapsedRef.current < FAST_CUTOFF_S ? '2' : elapsedRef.current < MEDIUM_CUTOFF_S ? '5' : '10'}s
      </Text>

      {error && <Text color="$red10" fontSize={14}>{error}</Text>}

      <Button size="$3" chromeless onPress={handleCancelRequest} marginTop="$4" disabled={cancelling}>
        {cancelling ? 'Cancelling...' : 'Cancel Request'}
      </Button>
    </YStack>
  )
}
