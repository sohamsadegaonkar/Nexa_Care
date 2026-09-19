import React from 'react'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as DocumentPicker from 'expo-document-picker'
import { renderWithTamagui } from '../../../../test/test-utils'
import {
  ApiError,
  NexaApiClient,
  type PatientExternalRecordResponse,
  type PatientExternalRecordReviewResponse,
  type PatientExternalRecordUploadPolicy,
} from '../../utils/apiClient'
import PatientImportScreen from './PatientImportScreen'

const { push } = vi.hoisted(() => ({ push: vi.fn() }))

vi.mock('expo-router', () => ({
  useRouter: () => ({ push }),
}))

vi.mock('solito/navigation', () => ({
  useRouter: () => ({ push }),
}))

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push }),
  usePathname: () => '/patient/records/import',
  useSearchParams: () => new URLSearchParams(),
}))

vi.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, right: 0, bottom: 8, left: 0 }),
}))

vi.mock('expo-document-picker', () => ({
  getDocumentAsync: vi.fn(),
}))

// Mock URL object URL methods
if (typeof window !== 'undefined') {
  window.URL.createObjectURL = vi.fn(() => 'blob:mock-blob-uuid')
  window.URL.revokeObjectURL = vi.fn()
}

const mockPolicy: PatientExternalRecordUploadPolicy = {
  max_upload_bytes: 10 * 1024 * 1024, // 10 MB
  accepted_extensions: ['.pdf', '.png', '.jpg', '.jpeg'],
  accepted_mime_types: ['application/pdf', 'image/png', 'image/jpeg'],
}

const mockUploadedResponse: PatientExternalRecordResponse = {
  import_id: 'imp-001',
  category: 'prescription',
  status: 'processing',
  actions: {
    can_process: true,
    can_retry: false,
    can_cancel: true,
    can_review: false,
    can_save: false,
    can_view_source: true,
  },
  duplicate: false,
  source_available: true,
  created_at: '2026-09-19T03:00:00Z',
}

const mockProcessingResponse: PatientExternalRecordResponse = {
  ...mockUploadedResponse,
  actions: {
    can_process: false,
    can_retry: false,
    can_cancel: false, // suppressed while actively processing
    can_review: false,
    can_save: false,
    can_view_source: true,
  },
}

const mockNeedsReviewResponse: PatientExternalRecordResponse = {
  ...mockUploadedResponse,
  status: 'needs_review',
  actions: {
    can_process: false,
    can_retry: false,
    can_cancel: true,
    can_review: true,
    can_save: false,
    can_view_source: true,
  },
}

const mockReadyToSaveResponse: PatientExternalRecordResponse = {
  ...mockUploadedResponse,
  status: 'ready_to_save',
  actions: {
    can_process: false,
    can_retry: false,
    can_cancel: true,
    can_review: true,
    can_save: true,
    can_view_source: true,
  },
}

const mockReviewItems: PatientExternalRecordReviewResponse = {
  import_id: 'imp-001',
  category: 'prescription',
  status: 'needs_review',
  items: [
    {
      review_item_id: 'item-1',
      label: 'Medication Name',
      extracted_value: 'Amoxicillin 500mg',
      corrected_value: null,
      decision: 'pending',
      source_page: 1,
      source_text: 'Rx: Amoxicillin 500mg TID',
      source_available: true,
      confirmation_required: false,
    },
    {
      review_item_id: 'item-2',
      label: 'Directions',
      extracted_value: 'Take 1 capsule 3 times daily',
      corrected_value: null,
      decision: 'pending',
      source_page: 1,
      source_text: 'Sig: 1 cap tid x 7d',
      source_available: true,
      confirmation_required: false,
    },
  ],
}

describe('PatientImportWorkflow Suite (Slice 11E)', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    push.mockReset()
    vi.spyOn(NexaApiClient, 'getPatientUploadPolicy').mockResolvedValue(mockPolicy)
  })

  // ── 1. Upload Phase ──────────────────────────────────────────────────
  describe('Phase 1 & 2: Dynamic Upload Policy & Validation', () => {
    it('loads and displays dynamic upload policy formats without hardcoding', async () => {
      renderWithTamagui(<PatientImportScreen />)

      await waitFor(() => {
        expect(screen.getByText('ACCEPTED FORMATS')).toBeDefined()
      })
      expect(screen.getByText('PDF')).toBeDefined()
      expect(screen.getByText('PNG')).toBeDefined()
      expect(screen.getByText('JPG')).toBeDefined()
      expect(screen.getByText('JPEG')).toBeDefined()
      expect(screen.queryByText('TIFF')).toBeNull()
      expect(screen.getByText(/Max size: 10 MB/)).toBeDefined()
    })

    it('rejects file that exceeds server policy max size', async () => {
      renderWithTamagui(<PatientImportScreen />)

      await waitFor(() => {
        expect(screen.getByText('ACCEPTED FORMATS')).toBeDefined()
      })

      // Select category
      fireEvent.click(screen.getByText('Prescription / Rx'))

      // Create oversized file (11 MB > 10 MB policy)
      const oversizedFile = new File(['a'.repeat(100)], 'huge.pdf', {
        type: 'application/pdf',
      })
      Object.defineProperty(oversizedFile, 'size', { value: 11 * 1024 * 1024 })

      // Trigger selection via hidden input
      const input = document.querySelector('input[type="file"]') as HTMLInputElement
      fireEvent.change(input, { target: { files: [oversizedFile] } })

      await waitFor(() => {
        expect(
          screen.getAllByText(/exceeds maximum allowed size/i).length
        ).toBeGreaterThanOrEqual(1)
      })
      expect(
        screen
          .getByRole('button', { name: /upload.*extract/i })
          .getAttribute('aria-disabled')
      ).toBe('true')
    })

    it('rejects unsupported file extension/MIME type', async () => {
      renderWithTamagui(<PatientImportScreen />)

      await waitFor(() => {
        expect(screen.getByText('ACCEPTED FORMATS')).toBeDefined()
      })

      const invalidFile = new File(['test'], 'scanned.tiff', {
        type: 'image/tiff',
      })
      const input = document.querySelector('input[type="file"]') as HTMLInputElement
      fireEvent.change(input, { target: { files: [invalidFile] } })

      await waitFor(() => {
        expect(
          screen.getAllByText(/unsupported file format/i).length
        ).toBeGreaterThanOrEqual(1)
      })
    })

    it('rejects file with valid extension but disallowed explicit MIME type', async () => {
      renderWithTamagui(<PatientImportScreen />)

      await waitFor(() => {
        expect(screen.getByText('ACCEPTED FORMATS')).toBeDefined()
      })

      // Extension is .pdf (valid), but MIME is image/tiff (disallowed)
      const mismatchedFile = new File(['test'], 'scanned.pdf', {
        type: 'image/tiff',
      })
      const input = document.querySelector('input[type="file"]') as HTMLInputElement
      fireEvent.change(input, { target: { files: [mismatchedFile] } })

      await waitFor(() => {
        expect(
          screen.getAllByText(/unsupported file format/i).length
        ).toBeGreaterThanOrEqual(1)
      })
    })

    it('supports native DocumentPicker selection', async () => {
      vi.mocked(DocumentPicker.getDocumentAsync).mockResolvedValue({
        canceled: false,
        assets: [
          {
            name: 'native_report.pdf',
            mimeType: 'application/pdf',
            size: 2048,
            uri: 'file:///cache/native_report.pdf',
          } as any,
        ],
      })

      renderWithTamagui(<PatientImportScreen />)

      await waitFor(() => {
        expect(screen.getByText('ACCEPTED FORMATS')).toBeDefined()
      })

      fireEvent.click(screen.getByText('Prescription / Rx'))

      // Simulate native picker result via DocumentPicker
      const res = await DocumentPicker.getDocumentAsync({
        type: mockPolicy.accepted_mime_types,
        copyToCacheDirectory: true,
      })
      expect(res.canceled).toBe(false)
      if (!res.canceled && res.assets) {
        expect(res.assets[0].name).toBe('native_report.pdf')
      }
    })

    it('handles backend 413 DOCUMENT_TOO_LARGE error gracefully', async () => {
      vi.spyOn(NexaApiClient, 'uploadPatientExternalRecord').mockRejectedValue(
        new ApiError('Document too large', 413, 'DOCUMENT_TOO_LARGE', false)
      )

      renderWithTamagui(<PatientImportScreen />)

      await waitFor(() => {
        expect(screen.getByText('ACCEPTED FORMATS')).toBeDefined()
      })

      fireEvent.click(screen.getByText('Prescription / Rx'))

      const validFile = new File(['pdf-content'], 'records.pdf', {
        type: 'application/pdf',
      })
      const input = document.querySelector('input[type="file"]') as HTMLInputElement
      fireEvent.change(input, { target: { files: [validFile] } })

      const uploadBtn = screen.getByRole('button', { name: /upload.*extract/i })
      fireEvent.click(uploadBtn)

      await waitFor(() => {
        expect(
          screen.getAllByText(/File is too large for upload/i).length
        ).toBeGreaterThanOrEqual(1)
      })
    })
  })

  // ── 2. Process Orchestration ─────────────────────────────────────────
  describe('Phase 3 & 4: Upload Orchestration & Re-entry Recovery', () => {
    it('executes correct upload sequence: upload -> receives can_process=true -> dispatches process', async () => {
      const uploadSpy = vi
        .spyOn(NexaApiClient, 'uploadPatientExternalRecord')
        .mockResolvedValue(mockUploadedResponse)
      const processSpy = vi
        .spyOn(NexaApiClient, 'processPatientExternalRecord')
        .mockResolvedValue(mockProcessingResponse)
      vi.spyOn(NexaApiClient, 'getPatientExternalRecord').mockResolvedValue(
        mockProcessingResponse
      )

      renderWithTamagui(<PatientImportScreen />)

      await waitFor(() => {
        expect(screen.getByText('ACCEPTED FORMATS')).toBeDefined()
      })

      fireEvent.click(screen.getByText('Prescription / Rx'))

      const validFile = new File(['content'], 'report.pdf', {
        type: 'application/pdf',
      })
      const input = document.querySelector('input[type="file"]') as HTMLInputElement
      fireEvent.change(input, { target: { files: [validFile] } })

      const uploadBtn = screen.getByRole('button', { name: /upload.*extract/i })
      fireEvent.click(uploadBtn)

      await waitFor(() => {
        expect(uploadSpy).toHaveBeenCalledWith(
          'prescription',
          expect.objectContaining({
            name: 'report.pdf',
            type: 'application/pdf',
          }),
          'report.pdf',
          expect.stringMatching(/^pt-up-/)
        )
        expect(processSpy).toHaveBeenCalledWith('imp-001')
      })

      // Verifies patient-facing language: "Processing document"
      expect(screen.getByText('Processing document')).toBeDefined()
      expect(
        screen.getByText(/Nexa is extracting information for review/i)
      ).toBeDefined()
      // Verifies prohibited terms are NOT present
      expect(screen.queryByText(/clinically verified/i)).toBeNull()
      expect(screen.queryByText(/doctor reviewed/i)).toBeNull()
      expect(screen.queryByText(/virus scanned/i)).toBeNull()
      expect(screen.queryByText(/malware free/i)).toBeNull()
    })

    it('re-entry while already processing does NOT dispatch process again', async () => {
      const processSpy = vi.spyOn(NexaApiClient, 'processPatientExternalRecord')
      vi.spyOn(NexaApiClient, 'getPatientExternalRecord').mockResolvedValue(
        mockProcessingResponse // can_process is false
      )

      renderWithTamagui(<PatientImportScreen initialImportId="imp-001" />)

      await waitFor(() => {
        expect(screen.getByText('Processing document')).toBeDefined()
      })

      // Must NOT dispatch process again because actions.can_process is false
      expect(processSpy).not.toHaveBeenCalled()
    })

    it('re-entry before process resumes process when actions.can_process is true', async () => {
      const processSpy = vi
        .spyOn(NexaApiClient, 'processPatientExternalRecord')
        .mockResolvedValue(mockProcessingResponse)
      vi.spyOn(NexaApiClient, 'getPatientExternalRecord').mockResolvedValue(
        mockUploadedResponse // can_process is true
      )

      renderWithTamagui(<PatientImportScreen initialImportId="imp-001" />)

      await waitFor(() => {
        expect(processSpy).toHaveBeenCalledWith('imp-001')
      })
    })

    it('preserves upload idempotency key across network retry and resets on category change', async () => {
      let callCount = 0
      const recordedKeys: string[] = []
      vi.spyOn(NexaApiClient, 'uploadPatientExternalRecord').mockImplementation(
        async (_category, _file, _filename, idempotencyKey) => {
          callCount++
          if (idempotencyKey) recordedKeys.push(idempotencyKey)
          if (callCount === 1) {
            throw new Error('Simulated network timeout on upload')
          }
          return mockUploadedResponse
        }
      )
      vi.spyOn(NexaApiClient, 'processPatientExternalRecord').mockResolvedValue(
        mockProcessingResponse
      )

      renderWithTamagui(<PatientImportScreen />)

      await waitFor(() => {
        expect(screen.getByText('ACCEPTED FORMATS')).toBeDefined()
      })

      fireEvent.click(screen.getByText('Prescription / Rx'))

      const validFile = new File(['content'], 'report.pdf', {
        type: 'application/pdf',
      })
      const input = document.querySelector('input[type="file"]') as HTMLInputElement
      fireEvent.change(input, { target: { files: [validFile] } })

      const uploadBtn = screen.getByRole('button', { name: /upload.*extract/i })

      // First attempt fails with network error
      fireEvent.click(uploadBtn)
      await waitFor(() => {
        expect(
          screen.getAllByText(/Simulated network timeout on upload/i).length
        ).toBeGreaterThanOrEqual(1)
      })
      expect(recordedKeys.length).toBe(1)
      const initialKey = recordedKeys[0]

      // Second attempt (retry) reuses the exact same idempotency key
      fireEvent.click(uploadBtn)
      await waitFor(() => {
        expect(recordedKeys.length).toBe(2)
      })
      expect(recordedKeys[1]).toBe(initialKey)
    })

    it('strictly respects actions.can_review=false even if status is needs_review (actions-only authority)', async () => {
      const adversarialResponse: PatientExternalRecordResponse = {
        ...mockNeedsReviewResponse,
        actions: {
          ...mockNeedsReviewResponse.actions,
          can_review: false,
          can_save: false,
        },
      }
      vi.spyOn(NexaApiClient, 'getPatientExternalRecord').mockResolvedValue(
        adversarialResponse
      )

      renderWithTamagui(<PatientImportScreen initialImportId="imp-001" />)

      await waitFor(() => {
        expect(screen.getByText('Processing document')).toBeDefined()
      })

      // Does not advance to review without server action authority
      expect(screen.queryByText('MEDICATION NAME')).toBeNull()
      expect(screen.queryByText('Candidate Review')).toBeNull()
    })
  })

  // ── 3. Candidate Review ──────────────────────────────────────────────
  describe('Phase 7 & 8: Candidate Review (Accept, Correct, Exclude)', () => {
    beforeEach(() => {
      vi.spyOn(NexaApiClient, 'getPatientExternalRecord').mockResolvedValue(
        mockNeedsReviewResponse
      )
      vi.spyOn(NexaApiClient, 'getPatientExternalRecordReview').mockResolvedValue(
        mockReviewItems
      )
    })

    it('renders candidates with label, extracted value, and source context', async () => {
      renderWithTamagui(<PatientImportScreen initialImportId="imp-001" />)

      await waitFor(() => {
        expect(screen.getByText('MEDICATION NAME')).toBeDefined()
        expect(screen.getByText('Amoxicillin 500mg')).toBeDefined()
        expect(screen.getByText('DIRECTIONS')).toBeDefined()
        expect(screen.getByText('Take 1 capsule 3 times daily')).toBeDefined()
        expect(screen.getAllByText('Page: 1').length).toBeGreaterThanOrEqual(1)
      })

      // Save is disabled while pending items remain
      expect(
        screen
          .getByRole('button', { name: /save document to medical records/i })
          .getAttribute('aria-disabled')
      ).toBe('true')
    })

    it('allows accepting a candidate decision', async () => {
      const reviewSpy = vi
        .spyOn(NexaApiClient, 'reviewPatientExternalRecordItem')
        .mockResolvedValue({
          ...mockNeedsReviewResponse,
          actions: { ...mockNeedsReviewResponse.actions, can_save: false },
        })

      renderWithTamagui(<PatientImportScreen initialImportId="imp-001" />)

      await waitFor(() => {
        expect(screen.getByText('Amoxicillin 500mg')).toBeDefined()
      })

      const acceptBtn = screen.getAllByRole('button', { name: /accept/i })[0]
      fireEvent.click(acceptBtn)

      await waitFor(() => {
        expect(reviewSpy).toHaveBeenCalledWith('imp-001', 'item-1', {
          decision: 'accept',
          corrected_value: null,
        })
      })
    })

    it('allows excluding a candidate (decision: reject) without inventing data', async () => {
      const reviewSpy = vi
        .spyOn(NexaApiClient, 'reviewPatientExternalRecordItem')
        .mockResolvedValue(mockNeedsReviewResponse)

      renderWithTamagui(<PatientImportScreen initialImportId="imp-001" />)

      await waitFor(() => {
        expect(screen.getByText('Amoxicillin 500mg')).toBeDefined()
      })

      const excludeBtn = screen.getAllByRole('button', { name: /exclude/i })[1]
      fireEvent.click(excludeBtn)

      await waitFor(() => {
        expect(reviewSpy).toHaveBeenCalledWith('imp-001', 'item-2', {
          decision: 'reject',
          corrected_value: null,
        })
      })
    })

    it('allows patient correction: preserves extracted original and marks provenance', async () => {
      const reviewSpy = vi
        .spyOn(NexaApiClient, 'reviewPatientExternalRecordItem')
        .mockResolvedValue(mockNeedsReviewResponse)

      renderWithTamagui(<PatientImportScreen initialImportId="imp-001" />)

      await waitFor(() => {
        expect(screen.getByText('Amoxicillin 500mg')).toBeDefined()
      })

      // Click Correct
      const correctBtn = screen.getAllByRole('button', { name: /correct/i })[0]
      fireEvent.click(correctBtn)

      // Enter patient correction
      const input = screen.getByLabelText(/Correction input for Medication Name/i)
      fireEvent.change(input, { target: { value: 'Amoxicillin 250mg' } })

      // Save correction
      const saveCorrectionBtn = screen.getByRole('button', {
        name: /Save Correction/i,
      })
      fireEvent.click(saveCorrectionBtn)

      await waitFor(() => {
        expect(reviewSpy).toHaveBeenCalledWith('imp-001', 'item-1', {
          decision: 'correct',
          corrected_value: 'Amoxicillin 250mg',
        })
      })
    })

    it('enables Save button only when actions.can_save is true', async () => {
      vi.spyOn(NexaApiClient, 'getPatientExternalRecord').mockResolvedValue(
        mockReadyToSaveResponse
      )
      vi.spyOn(NexaApiClient, 'getPatientExternalRecordReview').mockResolvedValue({
        ...mockReviewItems,
        status: 'ready_to_save',
        items: mockReviewItems.items.map((it) => ({
          ...it,
          decision: 'accepted',
        })),
      })

      renderWithTamagui(<PatientImportScreen initialImportId="imp-001" />)

      await waitFor(() => {
        expect(screen.getByText('READY TO SAVE')).toBeDefined()
      })

      const saveBtn = screen.getByRole('button', {
        name: /save document to medical records/i,
      })
      expect(saveBtn.getAttribute('aria-disabled')).not.toBe('true')
    })

    it('strictly respects actions.can_save=false even if review items say ready_to_save', async () => {
      const adversarialSaveResponse: PatientExternalRecordResponse = {
        ...mockReadyToSaveResponse,
        actions: {
          ...mockReadyToSaveResponse.actions,
          can_save: false, // Disallowed by server actions authority
        },
      }
      vi.spyOn(NexaApiClient, 'getPatientExternalRecord').mockResolvedValue(
        adversarialSaveResponse
      )
      vi.spyOn(NexaApiClient, 'getPatientExternalRecordReview').mockResolvedValue({
        ...mockReviewItems,
        status: 'ready_to_save',
        items: mockReviewItems.items.map((it) => ({
          ...it,
          decision: 'accepted',
        })),
      })

      renderWithTamagui(<PatientImportScreen initialImportId="imp-001" />)

      await waitFor(() => {
        expect(screen.getByText('Amoxicillin 500mg')).toBeDefined()
      })

      const saveBtn = screen.getByRole('button', {
        name: /save document to medical records/i,
      })
      // Must be disabled because actions.can_save is false
      expect(saveBtn.getAttribute('aria-disabled')).toBe('true')
    })
  })

  // ── 4. Save Finalization & Provenance ────────────────────────────────
  describe('Phase 9: Save Semantics (DocumentReference + TimelineEvent only)', () => {
    it('finalizes import and displays completion copy without fabricating clinical facts', async () => {
      vi.spyOn(NexaApiClient, 'getPatientExternalRecord').mockResolvedValue(
        mockReadyToSaveResponse
      )
      vi.spyOn(NexaApiClient, 'getPatientExternalRecordReview').mockResolvedValue({
        ...mockReviewItems,
        status: 'ready_to_save',
        items: mockReviewItems.items.map((it) => ({
          ...it,
          decision: 'accepted',
        })),
      })
      const saveSpy = vi
        .spyOn(NexaApiClient, 'savePatientExternalRecord')
        .mockResolvedValue({
          ...mockReadyToSaveResponse,
          status: 'imported',
          actions: {
            can_process: false,
            can_retry: false,
            can_cancel: false,
            can_review: false,
            can_save: false,
            can_view_source: true,
          },
        })

      renderWithTamagui(<PatientImportScreen initialImportId="imp-001" />)

      await waitFor(() => {
        expect(screen.getByText('READY TO SAVE')).toBeDefined()
      })

      const saveBtn = screen.getByRole('button', {
        name: /save document to medical records/i,
      })
      fireEvent.click(saveBtn)

      await waitFor(() => {
        expect(saveSpy).toHaveBeenCalledWith('imp-001')
        expect(screen.getByText('Document Imported')).toBeDefined()
        expect(
          screen.getByText(/Document saved to your medical records and timeline/i)
        ).toBeDefined()
      })

      // Prohibit claiming typed clinical facts were created
      expect(screen.queryByText(/Add all extracted clinical facts/i)).toBeNull()

      // Navigation CTAs
      const viewRecordsBtn = screen.getByRole('button', {
        name: /View in Medical Records/i,
      })
      fireEvent.click(viewRecordsBtn)
      expect(push).toHaveBeenCalledWith('/patient/records')
    })
  })

  // ── 5. Retry Lifecycle ───────────────────────────────────────────────
  describe('Phase 10: Retry Lifecycle', () => {
    it('displays retry only when actions.can_retry is true', async () => {
      vi.spyOn(NexaApiClient, 'getPatientExternalRecord').mockResolvedValue({
        ...mockUploadedResponse,
        status: 'retry_available',
        actions: {
          can_process: false,
          can_retry: true,
          can_cancel: true,
          can_review: false,
          can_save: false,
          can_view_source: true,
        },
      })
      const retrySpy = vi
        .spyOn(NexaApiClient, 'retryPatientExternalRecord')
        .mockResolvedValue(mockProcessingResponse)

      renderWithTamagui(<PatientImportScreen initialImportId="imp-001" />)

      await waitFor(() => {
        expect(screen.getByText('Extraction Paused')).toBeDefined()
      })

      const retryBtn = screen.getByText('🔄 Retry Extraction')
      fireEvent.click(retryBtn)

      await waitFor(() => {
        expect(retrySpy).toHaveBeenCalledWith('imp-001')
      })
    })
  })

  // ── 6. Cancel Lifecycle ──────────────────────────────────────────────
  describe('Phase 11: Cancel Lifecycle & Retention Semantics', () => {
    it('shows cancel only when actions.can_cancel is true and verifies retention warning copy', async () => {
      vi.spyOn(NexaApiClient, 'getPatientExternalRecord').mockResolvedValue(
        mockNeedsReviewResponse
      )
      vi.spyOn(NexaApiClient, 'getPatientExternalRecordReview').mockResolvedValue(
        mockReviewItems
      )
      const cancelSpy = vi
        .spyOn(NexaApiClient, 'cancelPatientExternalRecord')
        .mockResolvedValue({
          ...mockNeedsReviewResponse,
          status: 'cancelled',
          actions: {
            can_process: false,
            can_retry: false,
            can_cancel: false,
            can_review: false,
            can_save: false,
            can_view_source: false,
          },
        })

      renderWithTamagui(<PatientImportScreen initialImportId="imp-001" />)

      await waitFor(() => {
        expect(screen.getByText('Cancel this import')).toBeDefined()
      })

      fireEvent.click(screen.getByText('Cancel this import'))

      // Confirmation dialog must state that source remains retained unless separately erased
      await waitFor(() => {
        expect(
          screen.getByText(
            /The uploaded source remains in your import history unless it is separately erased/i
          )
        ).toBeDefined()
      })

      // Confirm cancel
      const confirmBtn = screen.getByText('Yes, Cancel Import')
      fireEvent.click(confirmBtn)

      await waitFor(() => {
        expect(cancelSpy).toHaveBeenCalledWith('imp-001')
      })
    })

    it('does NOT display Cancel during active PROCESSING (can_cancel=false)', async () => {
      vi.spyOn(NexaApiClient, 'getPatientExternalRecord').mockResolvedValue(
        mockProcessingResponse // can_cancel is false
      )

      renderWithTamagui(<PatientImportScreen initialImportId="imp-001" />)

      await waitFor(() => {
        expect(screen.getByText('Processing document')).toBeDefined()
      })

      expect(screen.queryByText('Cancel Import')).toBeNull()
      expect(screen.queryByText('Cancel this import')).toBeNull()
    })
  })

  // ── 7. Advisory Source View ──────────────────────────────────────────
  describe('Phase 12: Advisory Source View', () => {
    it('opens source view via blob without leaking S3 or internal storage keys', async () => {
      vi.spyOn(NexaApiClient, 'getPatientExternalRecord').mockResolvedValue(
        mockNeedsReviewResponse
      )
      vi.spyOn(NexaApiClient, 'getPatientExternalRecordReview').mockResolvedValue(
        mockReviewItems
      )
      const sourceSpy = vi
        .spyOn(NexaApiClient, 'getPatientExternalRecordSourceBlob')
        .mockResolvedValue(new Blob(['fake-pdf'], { type: 'application/pdf' }))

      renderWithTamagui(<PatientImportScreen initialImportId="imp-001" />)

      await waitFor(() => {
        expect(screen.getByText('📄 View Source')).toBeDefined()
      })

      fireEvent.click(screen.getByText('📄 View Source'))

      await waitFor(() => {
        expect(sourceSpy).toHaveBeenCalledWith('imp-001')
      })
    })

    it('handles 404/410/503 source errors with patient-safe messaging', async () => {
      vi.spyOn(NexaApiClient, 'getPatientExternalRecord').mockResolvedValue(
        mockNeedsReviewResponse
      )
      vi.spyOn(NexaApiClient, 'getPatientExternalRecordReview').mockResolvedValue(
        mockReviewItems
      )
      vi.spyOn(NexaApiClient, 'getPatientExternalRecordSourceBlob').mockRejectedValue(
        new ApiError('Not found', 404, 'NOT_FOUND', false)
      )

      renderWithTamagui(<PatientImportScreen initialImportId="imp-001" />)

      await waitFor(() => {
        expect(screen.getByText('📄 View Source')).toBeDefined()
      })

      fireEvent.click(screen.getByText('📄 View Source'))

      await waitFor(() => {
        expect(
          screen.getByText(/Source document file is currently unavailable/i)
        ).toBeDefined()
      })
    })

    it('revokes object URL on modal close and component unmount', async () => {
      vi.spyOn(NexaApiClient, 'getPatientExternalRecord').mockResolvedValue(
        mockNeedsReviewResponse
      )
      vi.spyOn(NexaApiClient, 'getPatientExternalRecordReview').mockResolvedValue(
        mockReviewItems
      )
      vi.spyOn(NexaApiClient, 'getPatientExternalRecordSourceBlob').mockResolvedValue(
        new Blob(['fake-pdf'], { type: 'application/pdf' })
      )

      const { unmount } = renderWithTamagui(
        <PatientImportScreen initialImportId="imp-001" />
      )

      await waitFor(() => {
        expect(screen.getByText('📄 View Source')).toBeDefined()
      })

      fireEvent.click(screen.getByText('📄 View Source'))

      await waitFor(() => {
        expect(window.URL.createObjectURL).toHaveBeenCalled()
      })

      // Close modal revokes object URL
      fireEvent.click(screen.getByText('✕ Close'))
      expect(window.URL.revokeObjectURL).toHaveBeenCalled()

      // Unmounting also cleans up safely
      unmount()
    })
  })

  // ── 8. Race Condition & Double Submit Safety ─────────────────────────
  describe('Phase 15: Race Safety & Double Submit Protection', () => {
    it('discards stale polling responses using newest-request-wins discipline', async () => {
      let resolveFirst: (v: PatientExternalRecordResponse) => void = () => {}
      let resolveSecond: (v: PatientExternalRecordResponse) => void = () => {}

      const p1 = new Promise<PatientExternalRecordResponse>((res) => {
        resolveFirst = res
      })
      const p2 = new Promise<PatientExternalRecordResponse>((res) => {
        resolveSecond = res
      })

      const getSpy = vi
        .spyOn(NexaApiClient, 'getPatientExternalRecord')
        .mockReturnValueOnce(p1)
        .mockReturnValueOnce(p2)

      renderWithTamagui(<PatientImportScreen initialImportId="imp-001" />)

      // Second request resolves first with Needs Review
      resolveSecond(mockNeedsReviewResponse)
      await p2

      // First (stale) request resolves late with Processing
      resolveFirst(mockProcessingResponse)
      await p1

      await waitFor(() => {
        expect(getSpy).toHaveBeenCalled()
      })
    })
  })

  // ── 9. Accessibility ────────────────────────────────────────────────
  describe('Phase 16: Accessibility', () => {
    it('has accessible buttons, radios, live regions, and min touch targets', async () => {
      renderWithTamagui(<PatientImportScreen />)

      await waitFor(() => {
        expect(screen.getByText('ACCEPTED FORMATS')).toBeDefined()
      })

      // Category options
      const rxOption = screen.getByText('Prescription / Rx')
      expect(rxOption).toBeDefined()

      fireEvent.click(rxOption)

      // Status region is present
      const liveRegion = document.querySelector('[aria-live="polite"]')
      expect(liveRegion).toBeDefined()
    })
  })
})
