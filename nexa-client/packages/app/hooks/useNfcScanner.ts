'use client'

import { useCallback, useState } from 'react'
import { Platform } from 'react-native'

import { NfcResolveError, resolveNfcCard, type NfcResolveResponse } from '../api/nfc'

export const WEB_MOCK_NFC_CARD_UID = 'MOCK-NFC-UID-001'

export type NfcScannerStatus = 'idle' | 'scanning' | 'success' | 'error'

export type NativeNfcUidReader = () => Promise<string>

export interface UseNfcScannerResult {
  status: NfcScannerStatus
  patientId: string | null
  errorMessage: string | null
  isScanning: boolean
  startScan: (cardUid?: string) => Promise<NfcResolveResponse | null>
  reset: () => void
}

let nativeNfcUidReader: NativeNfcUidReader | null = null

/**
 * Registers the platform NFC adapter used by native apps to read a card UID.
 */
export function setNativeNfcUidReader(reader: NativeNfcUidReader | null): void {
  nativeNfcUidReader = reader
}

function getReadableErrorMessage(error: unknown): string {
  if (error instanceof NfcResolveError) {
    return error.message
  }

  if (error instanceof Error) {
    return error.message
  }

  return 'Unable to scan NFC card.'
}

async function getCardUidForScan(cardUid?: string): Promise<string> {
  if (cardUid) {
    return cardUid
  }

  if (Platform.OS === 'web') {
    return WEB_MOCK_NFC_CARD_UID
  }

  if (!nativeNfcUidReader) {
    throw new NfcResolveError('Native NFC reader is not configured.', 'NFC_RESOLVE_FAILED', false)
  }

  return nativeNfcUidReader()
}

/**
 * Shared NFC scanner state machine for web simulation and native UID resolution.
 */
export function useNfcScanner(): UseNfcScannerResult {
  const [status, setStatus] = useState<NfcScannerStatus>('idle')
  const [patientId, setPatientId] = useState<string | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const reset = useCallback((): void => {
    setStatus('idle')
    setPatientId(null)
    setErrorMessage(null)
  }, [])

  const startScan = useCallback(async (cardUid?: string): Promise<NfcResolveResponse | null> => {
    setStatus('scanning')
    setPatientId(null)
    setErrorMessage(null)

    try {
      const uid = await getCardUidForScan(cardUid)
      const response = await resolveNfcCard(uid)

      setPatientId(response.patient_id)
      setStatus('success')

      return response
    } catch (error: unknown) {
      setErrorMessage(getReadableErrorMessage(error))
      setStatus('error')

      return null
    }
  }, [])

  return {
    status,
    patientId,
    errorMessage,
    isScanning: status === 'scanning',
    startScan,
    reset,
  }
}
