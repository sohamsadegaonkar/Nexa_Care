'use client'

import { useCallback, useState } from 'react'
import { Platform } from 'react-native'
import { NfcResolveError, resolveNfcCard, type NfcResolveResponse } from '../services/nfcResolve'

export const WEB_MOCK_NFC_CARD_UID = 'MOCK-NFC-UID-001'
export type NfcScannerStatus = 'idle' | 'scanning' | 'success' | 'error'
export type NativeNfcUidReader = () => Promise<string>
export interface UseNfcScannerResult {
  status: NfcScannerStatus
  discoveryHandle: string | null
  expiresAt: string | null
  errorMessage: string | null
  isScanning: boolean
  startScan: (cardUid?: string) => Promise<NfcResolveResponse | null>
  reset: () => void
}
let nativeNfcUidReader: NativeNfcUidReader | null = null
export function setNativeNfcUidReader(reader: NativeNfcUidReader | null): void {
  nativeNfcUidReader = reader
}
async function cardUidForScan(cardUid?: string): Promise<string> {
  if (cardUid) return cardUid
  if (Platform.OS === 'web') return WEB_MOCK_NFC_CARD_UID
  if (!nativeNfcUidReader)
    throw new NfcResolveError('Native NFC reader is not configured.', 'NFC_RESOLVE_FAILED', false)
  return nativeNfcUidReader()
}
export function useNfcScanner(): UseNfcScannerResult {
  const [status, setStatus] = useState<NfcScannerStatus>('idle')
  const [discoveryHandle, setDiscoveryHandle] = useState<string | null>(null)
  const [expiresAt, setExpiresAt] = useState<string | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const reset = useCallback(() => {
    setStatus('idle')
    setDiscoveryHandle(null)
    setExpiresAt(null)
    setErrorMessage(null)
  }, [])
  const startScan = useCallback(
    async (cardUid?: string): Promise<NfcResolveResponse | null> => {
      reset()
      setStatus('scanning')
      try {
        const response = await resolveNfcCard(await cardUidForScan(cardUid))
        setDiscoveryHandle(response.discovery_handle)
        setExpiresAt(response.expires_at)
        setStatus('success')
        return response
      } catch (error: unknown) {
        setErrorMessage(
          error instanceof NfcResolveError ? error.message : 'Unable to scan NFC card.'
        )
        setStatus('error')
        return null
      }
    },
    [reset]
  )
  return {
    status,
    discoveryHandle,
    expiresAt,
    errorMessage,
    isScanning: status === 'scanning',
    startScan,
    reset,
  }
}
