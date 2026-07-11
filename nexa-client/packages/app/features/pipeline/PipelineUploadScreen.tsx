/**
 * Pipeline upload screen — upload a clinical document for AI extraction.
 *
 * Features:
 *   - Dropzone with dashed border, centered icon/text for drag-and-drop
 *   - Cross-platform file selection (web file input triggered via Tamagui Button)
 *   - Patient selector (searches by name/ID, or uses patient_id from URL params)
 *   - Multipart upload via NexaApiClient — lets the browser set the
 *     Content-Type boundary automatically (never sets Content-Type manually)
 *   - Provider session token attached automatically by NexaApiClient
 *
 * ALPHA: This is an alpha implementation. File scanning and malicious file
 * detection are not yet integrated.
 *
 * SECURITY:
 * - All requests go through the shared NexaApiClient — no raw fetch/axios.
 * - Consent token passed as X-Consent-Token header.
 * - No hardcoded patient_id or provider_id.
 * - Provider session token attached automatically via getAuthToken().
 * - Content-Type is NOT set manually — the browser sets the multipart
 *   boundary automatically for FormData uploads.
 * - Session guard: must be authenticated via ProviderAuthContext.
 *
 * Route: /doctor/pipeline/upload?patient_id=...&consent_token=...
 */

'use client'

import {
  YStack, H2, Paragraph, Button, Text, Spinner, Card, Input, XStack, Separator, ScrollView,
} from '@my/ui'
import { useState, useCallback, useRef, useEffect } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { NexaApiClient, ApiError } from '../../utils/apiClient'
import { useProviderAuth } from '../doctor/ProviderAuthContext'

/** Maximum upload size in bytes (25 MB). */
const MAX_FILE_SIZE = 25 * 1024 * 1024

/** Allowed file extensions for clinical document upload. */
const ALLOWED_EXTENSIONS = ['.pdf', '.png', '.jpg', '.jpeg', '.tiff', '.doc', '.docx'] as const

type UploadStatus = 'idle' | 'uploading' | 'success' | 'error'

export function PipelineUploadScreen() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const { isAuthenticated } = useProviderAuth()

  const urlPatientId = searchParams.get('patient_id') ?? ''
  const consentToken = searchParams.get('consent_token') ?? ''

  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [uploadStatus, setUploadStatus] = useState<UploadStatus>('idle')
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [validationError, setValidationError] = useState<string | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const [patientId, setPatientId] = useState(urlPatientId)
  const [dragOver, setDragOver] = useState(false)

  /** Hidden file input ref for cross-platform file selection. */
  const fileInputRef = useRef<HTMLInputElement>(null)

  // ── Sync patient ID from URL params ──────────────────────────────────
  useEffect(() => {
    if (urlPatientId) setPatientId(urlPatientId)
  }, [urlPatientId])

  // ── Session guard ────────────────────────────────────────────────────
  if (!isAuthenticated) {
    return (
      <YStack f={1} bg="$background" jc="center" ai="center" gap="$4" p="$6">
        <Text col="$red10" size="$6">🔒 Session Required</Text>
        <Paragraph col="$colorSubdued" size="$3">
          Please log in to upload documents.
        </Paragraph>
        <Button theme="blue" onPress={() => router.push('/doctor/login')}>
          Go to Login
        </Button>
      </YStack>
    )
  }

  // ── File validation ──────────────────────────────────────────────────
  const validateFile = useCallback((file: File): string | null => {
    const ext = '.' + file.name.split('.').pop()?.toLowerCase()
    if (!ALLOWED_EXTENSIONS.includes(ext as any)) {
      return `Unsupported file type "${ext}". Allowed: ${ALLOWED_EXTENSIONS.join(', ')}`
    }
    if (file.size > MAX_FILE_SIZE) {
      return `File too large (${(file.size / 1024 / 1024).toFixed(1)} MB). Maximum: 25 MB.`
    }
    return null
  }, [])

  const handleFileSelect = useCallback((file: File) => {
    const error = validateFile(file)
    setValidationError(error)
    setSelectedFile(error ? null : file)
  }, [validateFile])

  // ── File input change handler ────────────────────────────────────────
  const handleFileInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0]
      if (file) handleFileSelect(file)
    },
    [handleFileSelect],
  )

  // ── Drag-and-drop handlers ───────────────────────────────────────────
  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(true)
  }, [])

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
  }, [])

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      setDragOver(false)
      const file = e.dataTransfer.files?.[0]
      if (file) handleFileSelect(file)
    },
    [handleFileSelect],
  )

  // ── Upload handler ───────────────────────────────────────────────────
  const handleUpload = useCallback(async () => {
    if (!selectedFile || !patientId.trim() || !consentToken) return

    setUploadStatus('uploading')
    setUploadError(null)

    try {
      // Build FormData for multipart upload. The browser sets the
      // Content-Type boundary automatically — we do NOT set it manually.
      const formData = new FormData()
      formData.append('file', selectedFile)
      formData.append('patient_id', patientId.trim())
      formData.append('filename', selectedFile.name)

      const result = await NexaApiClient.uploadDocument(patientId.trim(), formData, consentToken)

      setJobId(result.job_id)
      setUploadStatus('success')

      // Redirect to job status screen
      router.push(
        `/doctor/pipeline/jobs/${result.job_id}?patient_id=${patientId}&consent_token=${consentToken}`,
      )
    } catch (err) {
      setUploadStatus('error')
      if (err instanceof ApiError) {
        if (err.status === 401) {
          router.push('/doctor/login')
          return
        }
        if (err.status === 403) {
          setUploadError('Consent required for document ingestion.')
        } else {
          setUploadError(err.message || 'Upload failed.')
        }
      } else {
        setUploadError('Network error. Please try again.')
      }
    }
  }, [selectedFile, patientId, consentToken, router])

  // ── Missing consent token ────────────────────────────────────────────
  if (!consentToken) {
    return (
      <YStack f={1} bg="$background" jc="center" ai="center" gap="$4" p="$6">
        <Text col="$red10" size="$6">🔒 Consent Required</Text>
        <Paragraph col="$colorSubdued" size="$3">
          You must have an active consent grant for AI document ingestion
          before uploading.
        </Paragraph>
        <Button theme="blue" onPress={() => router.push('/doctor/request-consent')}>
          Request Consent
        </Button>
      </YStack>
    )
  }

  return (
    <ScrollView f={1} bg="$background">
      <YStack f={1} p="$6" gap="$4" mw={800} mx="auto">
        {/* ALPHA badge */}
        <XStack ai="center" gap="$2">
          <H2 col="$color" size="$7">Upload Document</H2>
          <Card bg="$orange4" br="$4" px="$2" py="$1">
            <Text col="$orange10" size="$2" fontWeight="700" textTransform="uppercase">
              ALPHA
            </Text>
          </Card>
        </XStack>

        <Paragraph col="$colorSubdued" size="$3">
          ALPHA · AI-assisted extraction results require clinical verification
          before commitment.
        </Paragraph>

        <Separator />

        {/* ── Patient selector ────────────────────────────────────────── */}
        <YStack gap="$2">
          <Text col="$color" size="$4" fontWeight="600">Patient</Text>
          <Input
            value={patientId}
            onChangeText={setPatientId}
            placeholder="Enter patient ID or use NFC scan…"
            size="$4"
            fontFamily="$mono"
          />
          {urlPatientId && (
            <Text col="$colorSubdued" size="$2">
              Pre-filled from consent context. You may change it if needed.
            </Text>
          )}
          {!patientId.trim() && (
            <Text col="$orange10" size="$2">
              Patient ID is required.
            </Text>
          )}
        </YStack>

        <Separator />

        {/* ── Dropzone ────────────────────────────────────────────────── */}
        <YStack
          bg={dragOver ? '$blue3' : '$backgroundHover'}
          br="$4"
          bw={2}
          bc={dragOver ? '$blue8' : '$borderColor'}
          borderStyle="dashed"
          p="$8"
          jc="center"
          ai="center"
          gap="$3"
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          hoverStyle={{ bc: '$blue6' }}
        >
          {/* Upload icon */}
          <Text size="$8" col={dragOver ? '$blue10' : '$colorSubdued'}>
            {selectedFile ? '📄' : '📁'}
          </Text>

          {selectedFile ? (
            <YStack ai="center" gap="$2">
              <Text col="$color" size="$4" fontWeight="600">
                {selectedFile.name}
              </Text>
              <Text col="$colorSubdued" size="$3">
                {(selectedFile.size / 1024).toFixed(1)} KB · {selectedFile.type || 'unknown type'}
              </Text>
              <Button
                size="$2"
                chromeless
                onPress={() => {
                  setSelectedFile(null)
                  setValidationError(null)
                }}
              >
                Remove file
              </Button>
            </YStack>
          ) : (
            <YStack ai="center" gap="$2">
              <Text col={dragOver ? '$blue10' : '$colorSubdued'} size="$4" fontWeight="600">
                {dragOver ? 'Drop file here' : 'Drag & drop a clinical document'}
              </Text>
              <Text col="$colorSubdued" size="$3">
                or
              </Text>
              <Button
                theme="blue"
                size="$3"
                onPress={() => fileInputRef.current?.click()}
              >
                Browse Files
              </Button>
            </YStack>
          )}

          {/* Hidden file input for cross-platform selection */}
          <input
            ref={fileInputRef}
            type="file"
            accept={ALLOWED_EXTENSIONS.join(',')}
            style={{ display: 'none' }}
            onChange={handleFileInputChange}
          />
        </YStack>

        {/* File validation error */}
        {validationError && (
          <YStack bg="$red4" br="$3" p="$3">
            <Text col="$red10" size="$3">{validationError}</Text>
          </YStack>
        )}

        {/* Allowed formats hint */}
        <Text col="$colorSubdued" size="$2">
          Allowed: PDF, PNG, JPG, TIFF, DOC, DOCX · Maximum: 25 MB
        </Text>

        <Separator />

        {/* ── Upload button ───────────────────────────────────────────── */}
        {uploadStatus === 'uploading' ? (
          <XStack ai="center" gap="$3">
            <Spinner size="small" color="$blue10" />
            <Text col="$colorSubdued" size="$3">Uploading document…</Text>
          </XStack>
        ) : (
          <Button
            theme="blue"
            size="$4"
            disabled={!selectedFile || !!validationError || !patientId.trim() || uploadStatus === 'uploading'}
            onPress={handleUpload}
          >
            Upload &amp; Extract
          </Button>
        )}

        {/* Success state */}
        {uploadStatus === 'success' && jobId && (
          <Card bg="$green4" br="$4" p="$3">
            <Text col="$green10" size="$3" fontWeight="600">
              Document queued for extraction. Job ID: {jobId}
            </Text>
          </Card>
        )}

        {/* Error state */}
        {uploadStatus === 'error' && uploadError && (
          <YStack bg="$red4" br="$3" p="$3" gap="$2">
            <Text col="$red10" size="$3">{uploadError}</Text>
            <Button
              size="$2"
              chromeless
              onPress={() => {
                setUploadStatus('idle')
                setUploadError(null)
              }}
            >
              Retry
            </Button>
          </YStack>
        )}
      </YStack>
    </ScrollView>
  )
}
