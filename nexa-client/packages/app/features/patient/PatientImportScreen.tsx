import { useRouter } from 'solito/navigation'
import {
  Button,
  Dialog,
  H2,
  H3,
  H4,
  Input,
  Paragraph,
  Separator,
  Spinner,
  Text,
  XStack,
  YStack,
} from 'tamagui'
import React, { useCallback, useEffect, useId, useRef, useState } from 'react'
import { Platform, RefreshControl, ScrollView } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import * as DocumentPicker from 'expo-document-picker'
import {
  ApiError,
  NexaApiClient,
  type PatientExternalRecordActions,
  type PatientExternalRecordResponse,
  type PatientExternalRecordReviewItem,
  type PatientExternalRecordReviewResponse,
  type PatientExternalRecordUploadPolicy,
  type SelectedSourceFile,
} from '../../utils/apiClient'

// ── Category Definitions ──────────────────────────────────────────────

export interface ImportCategoryOption {
  slug:
    | 'prescription'
    | 'lab_report'
    | 'imaging_report'
    | 'discharge_summary'
    | 'other_medical_record'
  label: string
  icon: string
  description: string
}

export const IMPORT_CATEGORIES: ImportCategoryOption[] = [
  {
    slug: 'prescription',
    label: 'Prescription / Rx',
    icon: '💊',
    description: 'Medicinal prescriptions, dosage instructions, and pharmacy slips.',
  },
  {
    slug: 'lab_report',
    label: 'Lab Report',
    icon: '🔬',
    description: 'Blood work, pathology, urinalysis, and diagnostic test panels.',
  },
  {
    slug: 'imaging_report',
    label: 'Imaging / Radiology',
    icon: '🩻',
    description: 'X-ray, MRI, CT scan, ultrasound, and radiology impressions.',
  },
  {
    slug: 'discharge_summary',
    label: 'Discharge Summary',
    icon: '🏥',
    description: 'Hospital discharge records, treatment summaries, and surgical notes.',
  },
  {
    slug: 'other_medical_record',
    label: 'Other Medical Record',
    icon: '📄',
    description: 'Doctor letters, immunization cards, clinical notes, and other reports.',
  },
]

export type ImportStep = 'picker' | 'processing' | 'review' | 'completed'

export interface PatientImportScreenProps {
  initialImportId?: string | null
}

export default function PatientImportScreen({
  initialImportId = null,
}: PatientImportScreenProps) {
  const router = useRouter()
  const insets = useSafeAreaInsets()
  const errorLiveId = useId()
  const statusLiveId = useId()

  // ── Flow & State Variables ───────────────────────────────────────────
  const [step, setStep] = useState<ImportStep>('picker')
  const [importId, setImportId] = useState<string | null>(initialImportId)
  const [currentImport, setCurrentImport] =
    useState<PatientExternalRecordResponse | null>(null)

  // Upload policy
  const [policy, setPolicy] =
    useState<PatientExternalRecordUploadPolicy | null>(null)
  const [policyLoading, setPolicyLoading] = useState(true)
  const [policyError, setPolicyError] = useState<string | null>(null)

  // Picker selection
  const [selectedCategory, setSelectedCategory] = useState<
    ImportCategoryOption['slug'] | null
  >(null)
  const [selectedFile, setSelectedFile] = useState<SelectedSourceFile | null>(null)
  const [fileValidationError, setFileValidationError] = useState<string | null>(
    null
  )
  const [uploading, setUploading] = useState(false)
  const [uploadIdempotencyKey, setUploadIdempotencyKey] = useState<string | null>(null)

  // Processing & Polling
  const [processingError, setProcessingError] = useState<string | null>(null)
  const [pollingActive, setPollingActive] = useState(false)
  const [actionLoading, setActionLoading] = useState(false)
  const pollingReqIdRef = useRef(0)
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Review & Decisions
  const [reviewData, setReviewData] =
    useState<PatientExternalRecordReviewResponse | null>(null)
  const [reviewLoading, setReviewLoading] = useState(false)
  const [reviewError, setReviewError] = useState<string | null>(null)
  const [editingItem, setEditingItem] =
    useState<PatientExternalRecordReviewItem | null>(null)
  const [correctionValue, setCorrectionValue] = useState('')
  const [decidingItemId, setDecidingItemId] = useState<string | null>(null)

  // Cancel Confirmation Modal
  const [showCancelModal, setShowCancelModal] = useState(false)

  // Advisory Source Viewer Modal
  const [showSourceModal, setShowSourceModal] = useState(false)
  const [sourceLoading, setSourceLoading] = useState(false)
  const [sourceError, setSourceError] = useState<string | null>(null)
  const [sourceObjectUrl, setSourceObjectUrl] = useState<string | null>(null)
  const sourceObjectUrlRef = useRef<string | null>(null)

  // File input ref for web
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  // ── Step 1: Load Upload Policy ────────────────────────────────────────
  const loadPolicy = useCallback(async () => {
    setPolicyLoading(true)
    setPolicyError(null)
    try {
      const res = await NexaApiClient.getPatientUploadPolicy()
      setPolicy(res)
    } catch (err) {
      setPolicyError(
        err instanceof Error
          ? err.message
          : 'Unable to load upload requirements. Please try again.'
      )
    } finally {
      setPolicyLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadPolicy()
  }, [loadPolicy])

  // Clean up any active object URL on import change or unmount
  useEffect(() => {
    return () => {
      if (sourceObjectUrlRef.current) {
        URL.revokeObjectURL(sourceObjectUrlRef.current)
        sourceObjectUrlRef.current = null
      }
    }
  }, [importId])

  // ── Step 2: Validate Selected File Against Dynamic Policy ────────────
  const validateFile = useCallback(
    (file: SelectedSourceFile): string | null => {
      if (!policy) return null

      // Check size
      if (file.size > policy.max_upload_bytes) {
        const maxMb = (policy.max_upload_bytes / (1024 * 1024)).toFixed(1)
        return `File size (${(file.size / (1024 * 1024)).toFixed(1)} MB) exceeds maximum allowed size of ${maxMb} MB.`
      }

      const formats = policy.accepted_extensions
        .map((e) => e.replace('.', '').toUpperCase())
        .join(', ')

      // Check extension: must match an accepted extension
      const fileNameLower = file.name.toLowerCase()
      const hasValidExt = policy.accepted_extensions.some((ext) =>
        fileNameLower.endsWith(ext.toLowerCase())
      )
      if (!hasValidExt) {
        return `Unsupported file format. Supported formats: ${formats}.`
      }

      // Check MIME type: when present and non-generic, must match accepted MIME
      const mimeType = file.type ? file.type.toLowerCase().trim() : ''
      if (mimeType && mimeType !== 'application/octet-stream') {
        const hasValidMime = policy.accepted_mime_types.some(
          (mime) => mime.toLowerCase() === mimeType
        )
        if (!hasValidMime) {
          return `Unsupported file format. Supported formats: ${formats}.`
        }
      }

      return null
    },
    [policy]
  )

  const handleFileSelect = (file: SelectedSourceFile) => {
    const error = validateFile(file)
    setFileValidationError(error)
    setSelectedFile(error ? null : file)
    setUploadIdempotencyKey(null) // Intent changed: reset idempotency key
  }

  const handleCategorySelect = (catSlug: ImportCategoryOption['slug']) => {
    setSelectedCategory(catSlug)
    setUploadIdempotencyKey(null) // Intent changed: reset idempotency key
  }

  const handlePickNativeDocument = async () => {
    try {
      const types =
        policy?.accepted_mime_types && policy.accepted_mime_types.length > 0
          ? policy.accepted_mime_types
          : ['application/pdf', 'image/*']

      const result = await DocumentPicker.getDocumentAsync({
        type: types,
        copyToCacheDirectory: true,
      })

      if (!result.canceled && result.assets && result.assets.length > 0) {
        const asset = result.assets[0]
        const sourceFile: SelectedSourceFile = {
          name: asset.name,
          type: asset.mimeType || 'application/octet-stream',
          size: asset.size || 0,
          uri: asset.uri,
          file: asset.file,
        }
        handleFileSelect(sourceFile)
      }
    } catch (err) {
      const errMsg =
        err instanceof Error ? err.message : 'Failed to select document.'
      setFileValidationError(errMsg)
    }
  }

  const handleBrowsePress = () => {
    if (Platform.OS === 'web') {
      fileInputRef.current?.click()
    } else {
      void handlePickNativeDocument()
    }
  }

  // ── Handle Upload Submission ──────────────────────────────────────────
  const handleUploadSubmit = async () => {
    if (!selectedCategory || !selectedFile || uploading) return

    setUploading(true)
    setFileValidationError(null)

    try {
      let idempotencyKey = uploadIdempotencyKey
      if (!idempotencyKey) {
        idempotencyKey = `pt-up-${Date.now()}-${Math.random().toString(36).substring(2, 9)}`
        setUploadIdempotencyKey(idempotencyKey)
      }

      const res = await NexaApiClient.uploadPatientExternalRecord(
        selectedCategory,
        selectedFile,
        selectedFile.name,
        idempotencyKey
      )

      setCurrentImport(res)
      setImportId(res.import_id)
      setStep('processing')

      // Phase 3 Orchestration: If can_process is true, dispatch /process immediately!
      if (res.actions.can_process) {
        try {
          const processRes = await NexaApiClient.processPatientExternalRecord(
            res.import_id
          )
          setCurrentImport(processRes)
        } catch (processErr) {
          setProcessingError(
            processErr instanceof Error
              ? processErr.message
              : 'Extraction request failed. You can retry.'
          )
        }
      }
      setPollingActive(true)
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 413) {
          setFileValidationError('File is too large for upload.')
        } else {
          setFileValidationError(err.message || 'Upload failed. Please try again.')
        }
      } else {
        setFileValidationError(
          err instanceof Error ? err.message : 'Upload failed. Please try again.'
        )
      }
    } finally {
      setUploading(false)
    }
  }

  // ── Fetch Review Items ────────────────────────────────────────────────
  const loadReviewItems = useCallback(async (targetImportId: string) => {
    setReviewLoading(true)
    setReviewError(null)
    try {
      const reviewRes =
        await NexaApiClient.getPatientExternalRecordReview(targetImportId)
      setReviewData(reviewRes)
    } catch (err) {
      setReviewError(
        err instanceof Error ? err.message : 'Failed to load review items.'
      )
    } finally {
      setReviewLoading(false)
    }
  }, [])

  // ── Re-entry / Polling Loop ───────────────────────────────────────────
  const pollImportStatus = useCallback(
    async (targetImportId: string) => {
      const reqId = ++pollingReqIdRef.current

      try {
        const detail =
          await NexaApiClient.getPatientExternalRecord(targetImportId)
        if (reqId !== pollingReqIdRef.current) return

        setCurrentImport(detail)

        // Phase 4: Handle actions on re-entry/polling
        if (detail.actions.can_process) {
          try {
            const processRes =
              await NexaApiClient.processPatientExternalRecord(targetImportId)
            if (reqId === pollingReqIdRef.current) {
              setCurrentImport(processRes)
            }
          } catch {
            // Keep polling on next cycle
          }
        }

        if (detail.actions.can_review) {
          setPollingActive(false)
          setStep('review')
          void loadReviewItems(targetImportId)
          return
        }

        if (detail.actions.can_save) {
          setPollingActive(false)
          setStep('review')
          void loadReviewItems(targetImportId)
          return
        }

        if (detail.status === 'imported') {
          setPollingActive(false)
          setStep('completed')
          return
        }

        if (detail.actions.can_retry) {
          setPollingActive(false)
          return
        }

        if (detail.status === 'cancelled') {
          setPollingActive(false)
          return
        }

        if (pollingActive) {
          pollTimerRef.current = setTimeout(() => {
            void pollImportStatus(targetImportId)
          }, 2000)
        }
      } catch (err) {
        if (reqId === pollingReqIdRef.current) {
          setProcessingError(
            err instanceof Error ? err.message : 'Network error updating status.'
          )
        }
      }
    },
    [pollingActive, loadReviewItems]
  )

  useEffect(() => {
    if (pollingActive && importId) {
      void pollImportStatus(importId)
    }
    return () => {
      if (pollTimerRef.current) clearTimeout(pollTimerRef.current)
    }
  }, [pollingActive, importId, pollImportStatus])

  // Handle Initial Re-entry (if initialImportId provided)
  useEffect(() => {
    if (initialImportId) {
      setImportId(initialImportId)
      setStep('processing')
      setPollingActive(true)
    }
  }, [initialImportId])

  // ── Step 3: Review Decision Handlers ──────────────────────────────────
  const handleDecision = async (
    reviewItemId: string,
    decision: 'accept' | 'correct' | 'reject',
    correctedValue?: string | null
  ) => {
    if (!importId || decidingItemId) return

    setDecidingItemId(reviewItemId)
    try {
      const updatedImport = await NexaApiClient.reviewPatientExternalRecordItem(
        importId,
        reviewItemId,
        {
          decision,
          corrected_value: decision === 'correct' ? correctedValue : null,
        }
      )

      setCurrentImport(updatedImport)

      setReviewData((prev) => {
        if (!prev) return prev
        const updatedItems = prev.items.map((item) => {
          if (item.review_item_id === reviewItemId) {
            const mappedDecision: PatientExternalRecordReviewItem['decision'] =
              decision === 'reject'
                ? 'rejected'
                : decision === 'correct'
                  ? 'corrected'
                  : 'accepted'
            return {
              ...item,
              decision: mappedDecision,
              corrected_value:
                decision === 'correct' ? correctedValue || null : null,
            }
          }
          return item
        })
        return {
          ...prev,
          status: updatedImport.actions.can_save
            ? 'ready_to_save'
            : 'needs_review',
          items: updatedItems,
        }
      })

      setEditingItem(null)
      setCorrectionValue('')
    } catch (err) {
      setReviewError(
        err instanceof Error ? err.message : 'Failed to save decision.'
      )
    } finally {
      setDecidingItemId(null)
    }
  }

  // ── Step 3: Save Finalization ─────────────────────────────────────────
  const handleSaveImport = async () => {
    if (!importId || actionLoading || !currentImport?.actions.can_save) return

    setActionLoading(true)
    setReviewError(null)
    try {
      const res = await NexaApiClient.savePatientExternalRecord(importId)
      setCurrentImport(res)
      setStep('completed')
    } catch (err) {
      setReviewError(
        err instanceof Error
          ? err.message
          : 'Failed to save document to records.'
      )
    } finally {
      setActionLoading(false)
    }
  }

  // ── Retry Handler ─────────────────────────────────────────────────────
  const handleRetry = async () => {
    if (!importId || actionLoading || !currentImport?.actions.can_retry) return

    setActionLoading(true)
    setProcessingError(null)
    try {
      const res = await NexaApiClient.retryPatientExternalRecord(importId)
      setCurrentImport(res)
      setStep('processing')
      setPollingActive(true)
    } catch (err) {
      setProcessingError(
        err instanceof Error ? err.message : 'Retry request failed.'
      )
    } finally {
      setActionLoading(false)
    }
  }

  // ── Cancel Handler ────────────────────────────────────────────────────
  const handleConfirmCancel = async () => {
    if (!importId || actionLoading || !currentImport?.actions.can_cancel) return

    setActionLoading(true)
    try {
      const res = await NexaApiClient.cancelPatientExternalRecord(importId)
      setCurrentImport(res)
      setShowCancelModal(false)
      setPollingActive(false)
    } catch (err) {
      setProcessingError(
        err instanceof Error ? err.message : 'Failed to cancel import.'
      )
    } finally {
      setActionLoading(false)
    }
  }

  // ── Advisory Source Viewer Handler ────────────────────────────────────
  const handleOpenSource = async () => {
    if (!importId || sourceLoading) return

    setShowSourceModal(true)
    if (Platform.OS !== 'web') {
      // Native disposition: truthful notice without calling browser URL.createObjectURL
      return
    }

    setSourceLoading(true)
    setSourceError(null)

    try {
      const blob = await NexaApiClient.getPatientExternalRecordSourceBlob(importId)
      if (sourceObjectUrlRef.current) {
        URL.revokeObjectURL(sourceObjectUrlRef.current)
        sourceObjectUrlRef.current = null
      }
      const url = URL.createObjectURL(blob)
      sourceObjectUrlRef.current = url
      setSourceObjectUrl(url)
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 404) {
          setSourceError('Source document file is currently unavailable.')
        } else if (err.status === 410) {
          setSourceError('Source document has expired or was removed.')
        } else if (err.status === 503) {
          setSourceError(
            'Document viewer service is temporarily unavailable. Please try again shortly.'
          )
        } else {
          setSourceError(
            err.message || 'Unable to load original source document.'
          )
        }
      } else {
        setSourceError('Network error while retrieving source document.')
      }
    } finally {
      setSourceLoading(false)
    }
  }

  const handleCloseSource = () => {
    setShowSourceModal(false)
    if (sourceObjectUrlRef.current) {
      URL.revokeObjectURL(sourceObjectUrlRef.current)
      sourceObjectUrlRef.current = null
      setSourceObjectUrl(null)
    }
    setSourceError(null)
  }

  // Reset entire flow for another upload
  const handleResetFlow = () => {
    setStep('picker')
    setImportId(null)
    setCurrentImport(null)
    setSelectedCategory(null)
    setSelectedFile(null)
    setUploadIdempotencyKey(null)
    setFileValidationError(null)
    setReviewData(null)
    setProcessingError(null)
    setReviewError(null)
  }

  // Keyboard Escape listener for modals
  useEffect(() => {
    if (Platform.OS !== 'web') return
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (showSourceModal) handleCloseSource()
        if (showCancelModal) setShowCancelModal(false)
        if (editingItem) setEditingItem(null)
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [showSourceModal, showCancelModal, editingItem])

  return (
    <YStack flex={1} backgroundColor="$background">
      {/* Header */}
      <YStack
        paddingHorizontal="$4"
        paddingTop={insets.top + 8}
        paddingBottom="$3"
        borderBottomWidth={1}
        borderBottomColor="$borderColor"
        backgroundColor="$background"
        gap="$1"
      >
        <XStack justifyContent="space-between" alignItems="center">
          <Button
            size="$2.5"
            chromeless
            onPress={() => router.push('/patient/records')}
            accessibilityRole="button"
            accessibilityLabel="Back to Medical Records"
          >
            ← Medical Records
          </Button>
          {importId && (
            <Text color="$color10" fontSize="$2" fontFamily="$body">
              Import #{importId.slice(0, 8)}
            </Text>
          )}
        </XStack>
        <H2 color="$color" fontSize="$6" fontWeight="800">
          Import Medical Record
        </H2>
        <Paragraph color="$color10" size="$3">
          Upload and safely integrate previous medical documents into your records.
        </Paragraph>
      </YStack>

      {/* Screen Reader Live Region for Status & Errors */}
      <YStack
        aria-live="polite"
        role="status"
        id={statusLiveId}
        style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden' }}
      >
        {step === 'processing' && 'Processing document. Information is being extracted.'}
        {step === 'review' &&
          (currentImport?.actions.can_save
            ? 'All items reviewed. Ready to save.'
            : 'Candidate review loaded. Please review each item.')}
        {step === 'completed' && 'Document successfully saved to medical records.'}
        {fileValidationError && `Error: ${fileValidationError}`}
        {processingError && `Error: ${processingError}`}
        {reviewError && `Error: ${reviewError}`}
      </YStack>

      <ScrollView
        style={{ flex: 1 }}
        contentContainerStyle={{ paddingBottom: insets.bottom + 40 }}
      >
        {/* STEP 1: PICKER */}
        {step === 'picker' && (
          <YStack padding="$4" gap="$4">
            {/* Policy Info Card */}
            {policyLoading ? (
              <XStack alignItems="center" gap="$2" padding="$3">
                <Spinner size="small" color="$color" />
                <Paragraph color="$color10" size="$2">
                  Checking document upload requirements...
                </Paragraph>
              </XStack>
            ) : policyError ? (
              <YStack
                backgroundColor="$red4"
                padding="$3"
                borderRadius="$3"
                gap="$2"
              >
                <Text color="$red10" fontWeight="700">
                  ⚠️ {policyError}
                </Text>
                <Button size="$2.5" onPress={loadPolicy}>
                  Retry Policy Check
                </Button>
              </YStack>
            ) : policy ? (
              <YStack
                backgroundColor="$backgroundHover"
                padding="$3"
                borderRadius="$3"
                gap="$2"
              >
                <XStack justifyContent="space-between" alignItems="center">
                  <Text color="$color11" fontSize="$2" fontWeight="700">
                    ACCEPTED FORMATS
                  </Text>
                  <Text color="$color10" fontSize="$2">
                    Max size: {Math.round(policy.max_upload_bytes / (1024 * 1024))} MB
                  </Text>
                </XStack>
                <XStack gap="$2" flexWrap="wrap">
                  {policy.accepted_extensions.map((ext) => (
                    <YStack
                      key={ext}
                      backgroundColor="$background"
                      paddingHorizontal="$2.5"
                      paddingVertical="$1"
                      borderRadius="$2"
                      borderWidth={1}
                      borderColor="$borderColor"
                    >
                      <Text
                        color="$color"
                        fontSize="$1"
                        fontWeight="800"
                      >
                        {ext.replace('.', '').toUpperCase()}
                      </Text>
                    </YStack>
                  ))}
                </XStack>
              </YStack>
            ) : null}

            {/* Step 1a: Select Category */}
            <YStack gap="$2">
              <H3 color="$color" fontSize="$4" fontWeight="800">
                1. Select Document Category
              </H3>
              <Paragraph color="$color10" size="$2">
                Choose the clinical classification that best matches your document.
              </Paragraph>
              <YStack gap="$2">
                {IMPORT_CATEGORIES.map((cat) => {
                  const isSelected = selectedCategory === cat.slug
                  return (
                    <XStack
                      key={cat.slug}
                      backgroundColor={
                        isSelected ? '$blue4' : '$backgroundHover'
                      }
                      borderWidth={isSelected ? 2 : 1}
                      borderColor={isSelected ? '$blue9' : '$borderColor'}
                      borderRadius="$4"
                      padding="$3.5"
                      alignItems="center"
                      justifyContent="space-between"
                      pressStyle={{ opacity: 0.85 }}
                      onPress={() => handleCategorySelect(cat.slug)}
                      accessibilityRole="radio"
                      accessibilityState={{ selected: isSelected }}
                      accessibilityLabel={cat.label}
                      aria-label={cat.label}
                      role="radio"
                      aria-checked={isSelected}
                      minHeight={44}
                    >
                      <XStack alignItems="center" gap="$3" flex={1}>
                        <Text fontSize={24}>{cat.icon}</Text>
                        <YStack flex={1}>
                          <Text
                            color={isSelected ? '$blue11' : '$color'}
                            fontSize="$4"
                            fontWeight="800"
                          >
                            {cat.label}
                          </Text>
                          <Paragraph
                            color={isSelected ? '$blue10' : '$color10'}
                            size="$2"
                          >
                            {cat.description}
                          </Paragraph>
                        </YStack>
                      </XStack>
                      {isSelected && (
                        <Text color="$blue10" fontSize="$4" fontWeight="800">
                          ✓
                        </Text>
                      )}
                    </XStack>
                  )
                })}
              </YStack>
            </YStack>

            <Separator />

            {/* Step 1b: Choose Document File */}
            <YStack gap="$2">
              <H3 color="$color" fontSize="$4" fontWeight="800">
                2. Choose Medical Document
              </H3>
              <Paragraph color="$color10" size="$2">
                Upload a clear copy of your report or prescription.
              </Paragraph>

              {/* Hidden file input for web */}
              {Platform.OS === 'web' && (
                <input
                  ref={fileInputRef}
                  type="file"
                  accept={policy?.accepted_extensions.join(',')}
                  style={{ display: 'none' }}
                  onChange={(e) => {
                    const files = e.target.files
                    if (files && files[0]) {
                      const file = files[0]
                      handleFileSelect({
                        name: file.name,
                        type: file.type || 'application/octet-stream',
                        size: file.size,
                        file: file,
                      })
                    }
                  }}
                />
              )}

              {selectedFile ? (
                <YStack
                  backgroundColor="$blue3"
                  borderWidth={1}
                  borderColor="$blue8"
                  borderRadius="$4"
                  padding="$4"
                  gap="$2"
                >
                  <XStack justifyContent="space-between" alignItems="center">
                    <XStack alignItems="center" gap="$2.5" flex={1}>
                      <Text fontSize={22}>📄</Text>
                      <YStack flex={1}>
                        <Text
                          color="$color"
                          fontSize="$3"
                          fontWeight="700"
                          numberOfLines={1}
                        >
                          {selectedFile.name}
                        </Text>
                        <Text color="$color10" fontSize="$1">
                          {(selectedFile.size / (1024 * 1024)).toFixed(2)} MB
                        </Text>
                      </YStack>
                    </XStack>
                    <Button
                      size="$2.5"
                      chromeless
                      onPress={() => {
                        setSelectedFile(null)
                        setFileValidationError(null)
                      }}
                      accessibilityRole="button"
                      accessibilityLabel="Remove selected file"
                    >
                      ✕ Remove
                    </Button>
                  </XStack>
                </YStack>
              ) : (
                <YStack
                  backgroundColor="$backgroundHover"
                  borderWidth={2}
                  borderStyle="dashed"
                  borderColor="$borderColor"
                  borderRadius="$4"
                  padding="$6"
                  alignItems="center"
                  gap="$3"
                  onPress={handleBrowsePress}
                  accessibilityRole="button"
                  accessibilityLabel="Browse medical document file"
                  pressStyle={{ opacity: 0.8 }}
                  minHeight={120}
                  justifyContent="center"
                >
                  <Text fontSize={32}>📁</Text>
                  <YStack alignItems="center" gap="$1">
                    <Text color="$color" fontSize="$4" fontWeight="800">
                      Select Medical Document
                    </Text>
                    <Paragraph color="$color10" size="$2" textAlign="center">
                      Tap to browse your device files
                    </Paragraph>
                  </YStack>
                  <Button
                    size="$3"
                    theme="blue"
                    onPress={handleBrowsePress}
                    accessibilityRole="button"
                    accessibilityLabel="Browse Files button"
                  >
                    Browse Document
                  </Button>
                </YStack>
              )}

              {/* Validation Error Banner */}
              {fileValidationError && (
                <YStack
                  backgroundColor="$red4"
                  padding="$3"
                  borderRadius="$3"
                  accessibilityRole="alert"
                >
                  <Text color="$red10" fontSize="$2" fontWeight="700">
                    ⚠️ {fileValidationError}
                  </Text>
                </YStack>
              )}
            </YStack>

            {/* Upload Button */}
            <YStack paddingTop="$2">
              <Button
                size="$4"
                theme="blue"
                disabled={!selectedCategory || !selectedFile || uploading}
                opacity={!selectedCategory || !selectedFile || uploading ? 0.5 : 1}
                onPress={handleUploadSubmit}
                accessibilityRole="button"
                accessibilityLabel="Upload and extract medical record"
                aria-label="Upload and extract medical record"
                minHeight={48}
              >
                {uploading ? (
                  <XStack alignItems="center" gap="$2">
                    <Spinner size="small" color="white" />
                    <Text color="white" fontWeight="800">
                      Uploading Document...
                    </Text>
                  </XStack>
                ) : (
                  <Text color="white" fontWeight="800">
                    Upload & Extract Document →
                  </Text>
                )}
              </Button>
            </YStack>
          </YStack>
        )}

        {/* STEP 2: PROCESSING & EXTRACTION */}
        {step === 'processing' && (
          <YStack padding="$5" gap="$5" alignItems="center">
            <YStack
              backgroundColor="$backgroundHover"
              padding="$6"
              borderRadius="$6"
              width="100%"
              alignItems="center"
              gap="$4"
            >
              <Spinner size="large" color="$blue10" />
              <YStack alignItems="center" gap="$1.5">
                <H3 color="$color" fontSize="$5" fontWeight="800">
                  Processing document
                </H3>
                <Paragraph
                  color="$color10"
                  size="$3"
                  textAlign="center"
                  maxWidth={380}
                >
                  Nexa is extracting information for review. This typically takes
                  a few moments.
                </Paragraph>
              </YStack>

              {/* Retry Card if Extraction Failed Retryable */}
              {currentImport?.actions.can_retry && (
                <YStack
                  backgroundColor="$yellow3"
                  borderWidth={1}
                  borderColor="$yellow8"
                  padding="$4"
                  borderRadius="$4"
                  width="100%"
                  gap="$2"
                  alignItems="center"
                >
                  <Text color="$yellow11" fontSize="$3" fontWeight="800">
                    Extraction Paused
                  </Text>
                  <Paragraph color="$yellow10" size="$2" textAlign="center">
                    Document extraction encountered a temporary issue. You can
                    retry the extraction.
                  </Paragraph>
                  <Button
                    size="$3"
                    theme="yellow"
                    disabled={actionLoading}
                    onPress={handleRetry}
                    accessibilityRole="button"
                    accessibilityLabel="Retry document extraction"
                  >
                    {actionLoading ? 'Retrying...' : '🔄 Retry Extraction'}
                  </Button>
                </YStack>
              )}

              {/* Cancel Import Button (if allowed by server actions) */}
              {currentImport?.actions.can_cancel && (
                <YStack paddingTop="$2">
                  <Button
                    size="$2.5"
                    chromeless
                    onPress={() => setShowCancelModal(true)}
                    accessibilityRole="button"
                    accessibilityLabel="Cancel this import"
                  >
                    Cancel Import
                  </Button>
                </YStack>
              )}

              {/* Status note */}
              {currentImport && (
                <Text color="$color10" fontSize="$1" opacity={0.6}>
                  Category: {currentImport.category} · Status:{' '}
                  {currentImport.status}
                </Text>
              )}

              {/* Processing Error Notice */}
              {processingError && (
                <YStack backgroundColor="$red4" padding="$3" borderRadius="$3">
                  <Text color="$red10" fontSize="$2" fontWeight="700">
                    ⚠️ {processingError}
                  </Text>
                </YStack>
              )}
            </YStack>
          </YStack>
        )}

        {/* STEP 3: CANDIDATE REVIEW */}
        {step === 'review' && (
          <YStack padding="$4" gap="$4">
            {/* Review Header */}
            <YStack
              backgroundColor="$backgroundHover"
              padding="$4"
              borderRadius="$4"
              gap="$2"
            >
              <XStack justifyContent="space-between" alignItems="center">
                <XStack alignItems="center" gap="$2">
                  <Text color="$color" fontSize="$4" fontWeight="800">
                    Extracted Information
                  </Text>
                  {currentImport?.actions.can_save ? (
                    <YStack
                      backgroundColor="$green4"
                      paddingHorizontal="$2.5"
                      paddingVertical="$1"
                      borderRadius="$2"
                    >
                      <Text color="$green10" fontSize="$1" fontWeight="800">
                        READY TO SAVE
                      </Text>
                    </YStack>
                  ) : (
                    <YStack
                      backgroundColor="$orange4"
                      paddingHorizontal="$2.5"
                      paddingVertical="$1"
                      borderRadius="$2"
                    >
                      <Text color="$orange10" fontSize="$1" fontWeight="800">
                        NEEDS REVIEW
                      </Text>
                    </YStack>
                  )}
                </XStack>

                {/* Advisory Source View Button */}
                {currentImport?.actions.can_view_source && (
                  <Button
                    size="$2.5"
                    theme="blue"
                    onPress={handleOpenSource}
                    accessibilityRole="button"
                    accessibilityLabel="View original source document"
                    aria-label="View original source document"
                  >
                    📄 View Source
                  </Button>
                )}
              </XStack>

              <Paragraph color="$color10" size="$2">
                Review each extracted field below. You can accept, correct, or
                exclude any item. All items must be reviewed before saving.
              </Paragraph>
            </YStack>

            {/* Error Banner */}
            {reviewError && (
              <YStack backgroundColor="$red4" padding="$3" borderRadius="$3">
                <Text color="$red10" fontSize="$2" fontWeight="700">
                  ⚠️ {reviewError}
                </Text>
              </YStack>
            )}

            {/* Candidate Items List */}
            {reviewLoading ? (
              <XStack justifyContent="center" padding="$6" gap="$2">
                <Spinner size="small" color="$color" />
                <Paragraph color="$color10" size="$3">
                  Loading extracted items...
                </Paragraph>
              </XStack>
            ) : reviewData && reviewData.items.length > 0 ? (
              <YStack gap="$3">
                {reviewData.items.map((item) => {
                  const isDeciding = decidingItemId === item.review_item_id
                  const isEditing = editingItem?.review_item_id === item.review_item_id

                  return (
                    <YStack
                      key={item.review_item_id}
                      backgroundColor="$backgroundHover"
                      borderWidth={1}
                      borderColor={
                        item.decision === 'accepted'
                          ? '$green7'
                          : item.decision === 'corrected'
                            ? '$blue7'
                            : item.decision === 'rejected'
                              ? '$red7'
                              : '$borderColor'
                      }
                      borderRadius="$4"
                      padding="$4"
                      gap="$3"
                    >
                      {/* Item Header & Provenance */}
                      <XStack
                        justifyContent="space-between"
                        alignItems="flex-start"
                      >
                        <YStack gap="$1" flex={1}>
                          <Text color="$color11" fontSize="$2" fontWeight="800">
                            {item.label.toUpperCase()}
                          </Text>
                          <Text
                            color="$color"
                            fontSize="$4"
                            fontWeight="700"
                          >
                            {item.extracted_value}
                          </Text>
                        </YStack>

                        {/* Decision Status Badge */}
                        <YStack>
                          {item.decision === 'accepted' && (
                            <YStack
                              backgroundColor="$green4"
                              paddingHorizontal="$2"
                              paddingVertical="$1"
                              borderRadius="$2"
                            >
                              <Text
                                color="$green10"
                                fontSize="$1"
                                fontWeight="800"
                              >
                                ✓ ACCEPTED
                              </Text>
                            </YStack>
                          )}
                          {item.decision === 'corrected' && (
                            <YStack
                              backgroundColor="$blue4"
                              paddingHorizontal="$2"
                              paddingVertical="$1"
                              borderRadius="$2"
                            >
                              <Text
                                color="$blue10"
                                fontSize="$1"
                                fontWeight="800"
                              >
                                ✎ CORRECTED
                              </Text>
                            </YStack>
                          )}
                          {item.decision === 'rejected' && (
                            <YStack
                              backgroundColor="$red4"
                              paddingHorizontal="$2"
                              paddingVertical="$1"
                              borderRadius="$2"
                            >
                              <Text color="$red10" fontSize="$1" fontWeight="800">
                                ✕ EXCLUDED
                              </Text>
                            </YStack>
                          )}
                          {item.decision === 'pending' && (
                            <YStack
                              backgroundColor="$orange4"
                              paddingHorizontal="$2"
                              paddingVertical="$1"
                              borderRadius="$2"
                            >
                              <Text
                                color="$orange10"
                                fontSize="$1"
                                fontWeight="800"
                              >
                                PENDING
                              </Text>
                            </YStack>
                          )}
                        </YStack>
                      </XStack>

                      {/* Corrected Value Display (Patient Provenance) */}
                      {item.decision === 'corrected' && item.corrected_value && (
                        <YStack
                          backgroundColor="$blue3"
                          padding="$2.5"
                          borderRadius="$3"
                          gap="$1"
                        >
                          <Text color="$blue10" fontSize="$1" fontWeight="700">
                            PATIENT CORRECTION (PROVENANCE)
                          </Text>
                          <Text color="$color" fontSize="$3" fontWeight="700">
                            {item.corrected_value}
                          </Text>
                          <Text color="$color10" fontSize="$1">
                            Original extracted value: {item.extracted_value}
                          </Text>
                        </YStack>
                      )}

                      {/* Evidence & Provenance Context */}
                      <XStack gap="$3" flexWrap="wrap" alignItems="center">
                        <Text color="$color10" fontSize="$1">
                          Source: Document extracted
                        </Text>
                        {item.source_page !== null && (
                          <Text color="$color10" fontSize="$1">
                            Page: {item.source_page}
                          </Text>
                        )}
                        {item.source_text && (
                          <Text
                            color="$color10"
                            fontSize="$1"
                            fontStyle="italic"
                            numberOfLines={1}
                          >
                            &quot;{item.source_text}&quot;
                          </Text>
                        )}
                      </XStack>

                      {/* Inline Correction Form */}
                      {isEditing ? (
                        <YStack
                          backgroundColor="$background"
                          padding="$3"
                          borderRadius="$3"
                          borderWidth={1}
                          borderColor="$blue8"
                          gap="$2.5"
                        >
                          <Text color="$color" fontSize="$2" fontWeight="700">
                            Enter patient correction:
                          </Text>
                          <Input
                            value={correctionValue}
                            onChangeText={setCorrectionValue}
                            placeholder="Type corrected value..."
                            size="$3"
                            autoFocus
                            accessibilityLabel={`Correction input for ${item.label}`}
                            aria-label={`Correction input for ${item.label}`}
                          />
                          <XStack gap="$2" justifyContent="flex-end">
                            <Button
                              size="$2.5"
                              chromeless
                              onPress={() => {
                                setEditingItem(null)
                                setCorrectionValue('')
                              }}
                            >
                              Cancel
                            </Button>
                            <Button
                              size="$2.5"
                              theme="blue"
                              disabled={!correctionValue.trim() || isDeciding}
                              onPress={() =>
                                handleDecision(
                                  item.review_item_id,
                                  'correct',
                                  correctionValue.trim()
                                )
                              }
                            >
                              {isDeciding ? 'Saving...' : 'Save Correction'}
                            </Button>
                          </XStack>
                        </YStack>
                      ) : (
                        /* Decision Action Buttons */
                        <XStack gap="$2" flexWrap="wrap">
                          <Button
                            size="$2.5"
                            theme={item.decision === 'accepted' ? 'green' : undefined}
                            borderWidth={1}
                            borderColor={
                              item.decision === 'accepted' ? '$green8' : '$borderColor'
                            }
                            disabled={isDeciding}
                            onPress={() =>
                              handleDecision(item.review_item_id, 'accept')
                            }
                            accessibilityRole="button"
                            accessibilityLabel={`Accept ${item.label}: ${item.extracted_value}`}
                            aria-label={`Accept ${item.label}: ${item.extracted_value}`}
                            minHeight={44}
                          >
                            {item.decision === 'accepted' ? '✓ Accepted' : 'Accept'}
                          </Button>
                          <Button
                            size="$2.5"
                            theme={item.decision === 'corrected' ? 'blue' : undefined}
                            borderWidth={1}
                            borderColor={
                              item.decision === 'corrected' ? '$blue8' : '$borderColor'
                            }
                            disabled={isDeciding}
                            onPress={() => {
                              setEditingItem(item)
                              setCorrectionValue(
                                item.corrected_value || item.extracted_value
                              )
                            }}
                            accessibilityRole="button"
                            accessibilityLabel={`Correct ${item.label}`}
                            aria-label={`Correct ${item.label}`}
                            minHeight={44}
                          >
                            {item.decision === 'corrected' ? '✎ Edit Correction' : 'Correct'}
                          </Button>
                          <Button
                            size="$2.5"
                            theme={item.decision === 'rejected' ? 'red' : undefined}
                            borderWidth={1}
                            borderColor={
                              item.decision === 'rejected' ? '$red8' : '$borderColor'
                            }
                            disabled={isDeciding}
                            onPress={() =>
                              handleDecision(item.review_item_id, 'reject')
                            }
                            accessibilityRole="button"
                            accessibilityLabel={`Exclude ${item.label}`}
                            aria-label={`Exclude ${item.label}`}
                            minHeight={44}
                          >
                            {item.decision === 'rejected' ? '✕ Excluded' : 'Exclude'}
                          </Button>
                        </XStack>
                      )}
                    </YStack>
                  )
                })}
              </YStack>
            ) : (
              <YStack
                backgroundColor="$backgroundHover"
                padding="$5"
                borderRadius="$4"
                alignItems="center"
              >
                <Paragraph color="$color10" size="$3" textAlign="center">
                  No extracted fields required manual review. You may proceed to
                  save the document.
                </Paragraph>
              </YStack>
            )}

            {/* Save Section */}
            <YStack
              paddingTop="$3"
              borderTopWidth={1}
              borderTopColor="$borderColor"
              gap="$2.5"
            >
              <Button
                size="$4"
                theme="blue"
                disabled={!currentImport?.actions.can_save || actionLoading}
                opacity={!currentImport?.actions.can_save || actionLoading ? 0.5 : 1}
                onPress={handleSaveImport}
                accessibilityRole="button"
                accessibilityLabel="Save document to medical records"
                minHeight={48}
              >
                {actionLoading ? (
                  <XStack alignItems="center" gap="$2">
                    <Spinner size="small" color="white" />
                    <Text color="white" fontWeight="800">
                      Saving Document...
                    </Text>
                  </XStack>
                ) : (
                  <Text color="white" fontWeight="800">
                    Save document to medical records →
                  </Text>
                )}
              </Button>

              {!currentImport?.actions.can_save && (
                <Paragraph color="$color10" size="$2" textAlign="center">
                  Please resolve all extracted items above (accept, correct, or
                  exclude) to enable saving.
                </Paragraph>
              )}
            </YStack>

            {/* Cancel Affordance in Review */}
            {currentImport?.actions.can_cancel && (
              <YStack alignItems="center" paddingTop="$2">
                <Button
                  size="$2.5"
                  chromeless
                  onPress={() => setShowCancelModal(true)}
                  accessibilityRole="button"
                  accessibilityLabel="Cancel this import"
                >
                  Cancel this import
                </Button>
              </YStack>
            )}
          </YStack>
        )}

        {/* STEP 4: COMPLETED */}
        {step === 'completed' && (
          <YStack padding="$5" gap="$5" alignItems="center">
            <YStack
              backgroundColor="$green3"
              borderWidth={1}
              borderColor="$green8"
              padding="$6"
              borderRadius="$6"
              width="100%"
              alignItems="center"
              gap="$4"
            >
              <Text fontSize={48}>✅</Text>
              <YStack alignItems="center" gap="$1.5">
                <H3 color="$green11" fontSize="$5" fontWeight="800">
                  Document Imported
                </H3>
                <Paragraph
                  color="$green10"
                  size="$3"
                  textAlign="center"
                  maxWidth={380}
                >
                  Document saved to your medical records and timeline.
                </Paragraph>
              </YStack>

              <Separator borderColor="$green6" width="100%" />

              <YStack width="100%" gap="$2.5">
                <Button
                  size="$4"
                  theme="blue"
                  onPress={() => router.push('/patient/records')}
                  accessibilityRole="button"
                  accessibilityLabel="View in Medical Records"
                  minHeight={48}
                >
                  View in Medical Records
                </Button>
                <Button
                  size="$3.5"
                  chromeless
                  onPress={() => router.push('/patient/timeline')}
                  accessibilityRole="button"
                  accessibilityLabel="View Health Timeline"
                >
                  View Health Timeline
                </Button>
                <Button
                  size="$3"
                  chromeless
                  onPress={handleResetFlow}
                  accessibilityRole="button"
                  accessibilityLabel="Import Another Document"
                >
                  + Import Another Document
                </Button>
              </YStack>
            </YStack>
          </YStack>
        )}
      </ScrollView>

      {/* ── CANCEL CONFIRMATION DIALOG ─────────────────────────────────── */}
      <Dialog
        modal
        open={showCancelModal}
        onOpenChange={(open) => setShowCancelModal(open)}
      >
        <Dialog.Portal>
          <Dialog.Overlay
            key="overlay"
            opacity={0.5}
            backgroundColor="black"
          />
          <Dialog.Content
            bordered
            elevate
            key="content"
            gap="$4"
            padding="$5"
            maxWidth={450}
            width="90%"
            backgroundColor="$background"
            borderRadius="$5"
            role="alertdialog"
          >
            <Dialog.Title color="$color" fontSize="$5" fontWeight="800">
              Cancel this import?
            </Dialog.Title>
            <Dialog.Description color="$color10" size="$3">
              Cancel this import? No information will be added to your medical
              records. The uploaded source remains in your import history unless
              it is separately erased.
            </Dialog.Description>
            <XStack gap="$3" justifyContent="flex-end" paddingTop="$2">
              <Button
                size="$3"
                chromeless
                disabled={actionLoading}
                onPress={() => setShowCancelModal(false)}
                accessibilityRole="button"
                accessibilityLabel="Do not cancel"
              >
                Keep Import
              </Button>
              <Button
                size="$3"
                theme="red"
                disabled={actionLoading}
                onPress={handleConfirmCancel}
                accessibilityRole="button"
                accessibilityLabel="Confirm cancel import"
              >
                {actionLoading ? 'Cancelling...' : 'Yes, Cancel Import'}
              </Button>
            </XStack>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog>

      {/* ── ADVISORY SOURCE VIEWER MODAL ───────────────────────────────── */}
      <Dialog
        modal
        open={showSourceModal}
        onOpenChange={(open) => {
          if (!open) handleCloseSource()
        }}
      >
        <Dialog.Portal>
          <Dialog.Overlay
            key="overlay"
            opacity={0.6}
            backgroundColor="black"
          />
          <Dialog.Content
            bordered
            elevate
            key="content"
            gap="$3"
            padding="$4"
            maxWidth={650}
            width="92%"
            height="80%"
            backgroundColor="$background"
            borderRadius="$5"
            role="dialog"
            accessibilityLabel="Original source document viewer"
          >
            <XStack justifyContent="space-between" alignItems="center">
              <H4 color="$color" fontSize="$4" fontWeight="800">
                Original Source Document
              </H4>
              <Button
                size="$2.5"
                chromeless
                onPress={handleCloseSource}
                accessibilityRole="button"
                accessibilityLabel="Close source document viewer"
              >
                ✕ Close
              </Button>
            </XStack>

            <Separator />

            {Platform.OS !== 'web' ? (
              <YStack
                flex={1}
                justifyContent="center"
                alignItems="center"
                gap="$3"
                padding="$4"
              >
                <Text fontSize={32}>📱</Text>
                <Paragraph color="$color10" size="$3" textAlign="center">
                  Document preview is available in the Nexa Care web portal. In-app native preview is currently in development.
                </Paragraph>
              </YStack>
            ) : sourceLoading ? (
              <YStack
                flex={1}
                justifyContent="center"
                alignItems="center"
                gap="$2"
              >
                <Spinner size="large" color="$color" />
                <Paragraph color="$color10" size="$2">
                  Retrieving source document...
                </Paragraph>
              </YStack>
            ) : sourceError ? (
              <YStack
                flex={1}
                justifyContent="center"
                alignItems="center"
                gap="$3"
                padding="$4"
              >
                <Text fontSize={32}>⚠️</Text>
                <Paragraph color="$red10" size="$3" textAlign="center">
                  {sourceError}
                </Paragraph>
                <Button size="$3" onPress={handleOpenSource}>
                  Retry Loading Source
                </Button>
              </YStack>
            ) : sourceObjectUrl ? (
              <YStack flex={1} borderRadius="$3" overflow="hidden">
                <iframe
                  src={sourceObjectUrl}
                  title="Source Document"
                  style={{ width: '100%', height: '100%', border: 'none' }}
                />
              </YStack>
            ) : null}
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog>
    </YStack>
  )
}
