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
 * Route: /doctor/pipeline/upload?patient_id=...&workflow_id=...
 */

'use client'

import {
  YStack, H2, Paragraph, Button, Text, Spinner, Card, Input, XStack, Separator, ScrollView,
} from '@my/ui'
import { useState, useCallback, useRef, useEffect } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { NexaApiClient, ApiError } from '../../utils/apiClient'
import { useProviderAuth } from '../doctor/ProviderAuthContext'
import { attachJobId, useCapability } from '../../services/capabilityStore'

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
  const workflowId = searchParams.get('workflow_id')
  const capability = useCapability(workflowId)
  const consentToken = capability?.token ?? ''

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
      <YStack flex={1} backgroundColor="$background" justifyContent="center" alignItems="center" gap="$4" padding="$6">
        <Text color="$red10" fontSize="$6">🔒 Session Required</Text>
        <Paragraph color="$color10" fontSize="$3">
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

      if (workflowId) attachJobId(workflowId, result.job_id)

      // Redirect to job status screen
      router.push(
        `/doctor/pipeline/jobs/${result.job_id}?patient_id=${patientId}&workflow_id=${workflowId}`,
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
  }, [selectedFile, patientId, consentToken, workflowId, router])

  // ── Missing consent token ────────────────────────────────────────────
  if (!consentToken) {
    return (
      <YStack flex={1} backgroundColor="$background" justifyContent="center" alignItems="center" gap="$4" padding="$6">
        <Text color="$red10" fontSize="$6">🔒 Consent Required</Text>
        <Paragraph color="$color10" fontSize="$3">
          {workflowId
            ? 'Access session expired — request access again.'
            : 'You must have an active consent grant for AI document ingestion before uploading.'}
        </Paragraph>
        <Button theme="blue" onPress={() => router.push('/doctor/request-consent')}>
          Request Consent
        </Button>
      </YStack>
    )
  }

  return (
    <ScrollView flex={1} backgroundColor="$background">
      <YStack flex={1} padding="$6" gap="$4" maxWidth={800} marginHorizontal="auto">
        {/* ALPHA badge */}
        <XStack alignItems="center" gap="$2">
          <H2 color="$color12" fontSize="$7">Upload Document</H2>
          <Card backgroundColor="$orange4" borderRadius="$4" paddingHorizontal="$2" paddingVertical="$1">
            <Text color="$orange10" fontSize="$2" fontWeight="700" textTransform="uppercase">
              ALPHA
            </Text>
          </Card>
        </XStack>

        <Paragraph color="$color10" fontSize="$3">
          ALPHA · AI-assisted extraction results require clinical verification
          before commitment.
        </Paragraph>

        <Separator />

        {/* ── Patient selector ────────────────────────────────────────── */}
        <YStack gap="$2">
          <Text color="$color12" fontSize="$4" fontWeight="600">Patient</Text>
          <Input
            value={patientId}
            onChangeText={setPatientId}
            placeholder="Enter patient ID or use NFC scan…"
            size="$4"
          />
          {urlPatientId && (
            <Text color="$color10" fontSize="$2">
              Pre-filled from consent context. You may change it if needed.
            </Text>
          )}
          {!patientId.trim() && (
            <Text color="$orange10" fontSize="$2">
              Patient ID is required.
            </Text>
          )}
        </YStack>

        <Separator />

        {/* ── Dropzone ────────────────────────────────────────────────── */}
        <YStack
          backgroundColor={dragOver ? '$blue3' : '$backgroundHover'}
          borderRadius="$4"
          borderWidth={2}
          borderColor={dragOver ? '$blue8' : '$borderColor'}
          borderStyle="dashed"
          padding="$8"
          justifyContent="center"
          alignItems="center"
          gap="$3"
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          hoverStyle={{ borderColor: '$blue6' }}
        >
          {/* Upload icon */}
          <Text fontSize="$8" color={dragOver ? '$blue10' : '$color10'}>
            {selectedFile ? '📄' : '📁'}
          </Text>

          {selectedFile ? (
            <YStack alignItems="center" gap="$2">
              <Text color="$color12" fontSize="$4" fontWeight="600">
                {selectedFile.name}
              </Text>
              <Text color="$color10" fontSize="$3">
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
            <YStack alignItems="center" gap="$2">
              <Text color={dragOver ? '$blue10' : '$color10'} fontSize="$4" fontWeight="600">
                {dragOver ? 'Drop file here' : 'Drag & drop a clinical document'}
              </Text>
              <Text color="$color10" fontSize="$3">
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
          <YStack backgroundColor="$red4" borderRadius="$3" padding="$3">
            <Text color="$red10" fontSize="$3">{validationError}</Text>
          </YStack>
        )}

        {/* Allowed formats hint */}
        <Text color="$color10" fontSize="$2">
          Allowed: PDF, PNG, JPG, TIFF, DOC, DOCX · Maximum: 25 MB
        </Text>

        <Separator />

        {/* ── Upload button ───────────────────────────────────────────── */}
        {uploadStatus === 'uploading' ? (
          <XStack alignItems="center" gap="$3">
            <Spinner size="small" color="$blue10" />
            <Text color="$color10" fontSize="$3">Uploading document…</Text>
          </XStack>
        ) : (
          <Button
            theme="blue"
            size="$4"
            disabled={!selectedFile || !!validationError || !patientId.trim()}
            onPress={handleUpload}
          >
            Upload &amp; Extract
          </Button>
        )}

        {/* Success state */}
        {uploadStatus === 'success' && jobId && (
          <Card backgroundColor="$green4" borderRadius="$4" padding="$3">
            <Text color="$green10" fontSize="$3" fontWeight="600">
              Document queued for extraction. Job ID: {jobId}
            </Text>
          </Card>
        )}

        {/* Error state */}
        {uploadStatus === 'error' && uploadError && (
          <YStack backgroundColor="$red4" borderRadius="$3" padding="$3" gap="$2">
            <Text color="$red10" fontSize="$3">{uploadError}</Text>
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