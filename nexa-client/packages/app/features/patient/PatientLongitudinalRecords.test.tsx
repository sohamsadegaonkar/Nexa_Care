import React from 'react'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithTamagui } from '../../../../test/test-utils'
import { NexaApiClient } from '../../utils/apiClient'
import PatientHealthHome from './PatientHealthHome'
import PatientRecordsScreen from './PatientRecordsScreen'
import PatientPrescriptionsScreen from './PatientPrescriptionsScreen'
import PatientReportsScreen from './PatientReportsScreen'
import PatientRecordDetailModal from './PatientRecordDetailModal'

const { push } = vi.hoisted(() => ({ push: vi.fn() }))

vi.mock('expo-router', () => ({
  useRouter: () => ({ push }),
}))

vi.mock('solito/navigation', () => ({
  useRouter: () => ({ push }),
}))

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push }),
  usePathname: () => '/patient/dashboard',
  useSearchParams: () => new URLSearchParams(),
}))

vi.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, right: 0, bottom: 8, left: 0 }),
}))

describe('PatientLongitudinalRecords UX Suite', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    push.mockReset()
  })

  describe('PatientHealthHome', () => {
    it('renders health summary with active medications and honest allergy label', async () => {
      vi.spyOn(NexaApiClient, 'getMyHealthSummary').mockResolvedValue({
        patient_id: 'pat-1',
        counts: {
          allergies: 1,
          medications: 1,
          vitals: 1,
          labs: 1,
          reports: 1,
        },
        allergy_highlights: [
          {
            allergen: 'Penicillin',
            severity: 'Severe',
            risk_level: 'HIGH_RISK',
            source: 'manual',
          },
        ],
        active_medications: [
          {
            medication_name: 'Metformin',
            dosage: '500mg',
            frequency: 'Twice daily',
            source: 'manual',
          },
        ],
        latest_vitals: [
          {
            type: 'BP',
            value: '120/80',
            unit: 'mmHg',
            source: 'manual',
          },
        ],
        recent_labs: [
          {
            test_name: 'HbA1c',
            value: '5.8',
            unit: '%',
            is_abnormal: false,
            source: 'ai_extracted',
          },
        ],
        recent_reports: [],
        recent_timeline_events: [],
        last_updated: '2026-09-16T12:00:00Z',
      })

      renderWithTamagui(<PatientHealthHome />)

      expect(await screen.findByText('Metformin')).toBeTruthy()
      expect(screen.getByText('500mg • Twice daily')).toBeTruthy()
      expect(screen.getByText('Penicillin')).toBeTruthy()
      expect(screen.getByText('BP')).toBeTruthy()
      expect(screen.getByText('120/80 mmHg')).toBeTruthy()
      expect(screen.getByText('HbA1c')).toBeTruthy()
    })

    it('renders honest "No recorded allergies on file" when empty, never fake "No allergies"', async () => {
      vi.spyOn(NexaApiClient, 'getMyHealthSummary').mockResolvedValue({
        patient_id: 'pat-1',
        counts: { allergies: 0, medications: 0, vitals: 0, labs: 0, reports: 0 },
        allergy_highlights: [],
        active_medications: [],
        latest_vitals: [],
        recent_labs: [],
        recent_reports: [],
        recent_timeline_events: [],
        last_updated: null,
      })

      renderWithTamagui(<PatientHealthHome />)

      expect(await screen.findByText('No recorded allergies on file.')).toBeTruthy()
      expect(screen.getByText('No active medications recorded on file.')).toBeTruthy()
    })

    it('renders error notice with Retry button and triggers retry on press', async () => {
      const summarySpy = vi
        .spyOn(NexaApiClient, 'getMyHealthSummary')
        .mockRejectedValueOnce(new Error('Network error loading summary'))
        .mockResolvedValueOnce({
          patient_id: 'pat-1',
          counts: { allergies: 0, medications: 0, vitals: 0, labs: 0, reports: 0 },
          allergy_highlights: [],
          active_medications: [],
          latest_vitals: [],
          recent_labs: [],
          recent_reports: [],
          recent_timeline_events: [],
          last_updated: null,
        })

      renderWithTamagui(<PatientHealthHome />)

      expect(await screen.findByText('Network error loading summary')).toBeTruthy()
      const retryBtn = screen.getByText('Retry')
      expect(retryBtn).toBeTruthy()
      fireEvent.click(retryBtn)

      await waitFor(() => {
        expect(summarySpy).toHaveBeenCalledTimes(2)
      })
      expect(await screen.findByText('No recorded allergies on file.')).toBeTruthy()
    })
  })

  describe('PatientRecordsScreen', () => {
    it('renders category overview and supports drilldown into category records', async () => {
      vi.spyOn(NexaApiClient, 'getMyRecordCategories').mockResolvedValue({
        patient_id: 'pat-1',
        categories: [
          {
            category: 'vitals',
            label: 'Vitals',
            icon: '❤️',
            count: 3,
            latest_record_date: '2026-07-17',
            preview: 'BP: 120/80 mmHg',
          },
          {
            category: 'medications',
            label: 'Medications',
            icon: '💊',
            count: 1,
            latest_record_date: '2026-07-10',
            preview: 'Metformin 500mg',
          },
        ],
      })

      vi.spyOn(NexaApiClient, 'getMyRecordsByCategory').mockResolvedValue({
        patient_id: 'pat-1',
        category: 'vitals',
        records: [
          {
            record_id: 'vital-1',
            category: 'vitals',
            type: 'BP',
            value: '120/80',
            unit: 'mmHg',
            recorded_at: '2026-07-17T10:00:00Z',
            source: 'manual',
          },
        ],
        next_cursor: null,
      })

      renderWithTamagui(<PatientRecordsScreen />)

      expect(await screen.findByText('Vitals')).toBeTruthy()
      expect(screen.getByText('Latest: BP: 120/80 mmHg')).toBeTruthy()

      // Click on Vitals category to drill down
      fireEvent.click(screen.getByText('Vitals'))

      expect(await screen.findByText('← All Categories')).toBeTruthy()
      expect(await screen.findByText('BP')).toBeTruthy()
      expect(screen.getByText('120/80 mmHg')).toBeTruthy()
    })

    it('supports in-category search, differentiated empty states, and reset action', async () => {
      vi.spyOn(NexaApiClient, 'getMyRecordCategories').mockResolvedValue({
        patient_id: 'pat-1',
        categories: [
          {
            category: 'vitals',
            label: 'Vitals',
            icon: '❤️',
            count: 2,
            latest_record_date: '2026-07-17',
            preview: 'BP: 120/80 mmHg',
          },
        ],
      })

      vi.spyOn(NexaApiClient, 'getMyRecordsByCategory').mockResolvedValue({
        patient_id: 'pat-1',
        category: 'vitals',
        records: [
          {
            record_id: 'vital-1',
            category: 'vitals',
            type: 'BP',
            value: '120/80',
            unit: 'mmHg',
            recorded_at: '2026-07-17T10:00:00Z',
            source: 'manual',
          },
          {
            record_id: 'vital-2',
            category: 'vitals',
            type: 'Heart Rate',
            value: '72',
            unit: 'bpm',
            recorded_at: '2026-07-17T10:00:00Z',
            source: 'manual',
          },
        ],
        next_cursor: null,
      })

      renderWithTamagui(<PatientRecordsScreen />)
      fireEvent.click(await screen.findByText('Vitals'))

      expect(await screen.findByText('BP')).toBeTruthy()
      expect(screen.getByText('Heart Rate')).toBeTruthy()

      // Search for Heart
      const searchInput = await screen.findByPlaceholderText('Search Vitals…')
      fireEvent.change(searchInput, { target: { value: 'Heart' } })

      expect(screen.queryByText('120/80 mmHg')).toBeNull()
      expect(screen.getByText('72 bpm')).toBeTruthy()

      // Search for non-existent
      fireEvent.change(searchInput, { target: { value: 'Nonexistent' } })
      expect(await screen.findByText('No records match "Nonexistent"')).toBeTruthy()

      // Clear search
      fireEvent.click(screen.getByText('Clear Search'))
      expect(await screen.findByText('BP')).toBeTruthy()
      expect(screen.getByText('Heart Rate')).toBeTruthy()
      expect(screen.getByText('✓ All Vitals records loaded')).toBeTruthy()
    })

    it('handles category records fetch error with retry button in drilldown', async () => {
      vi.spyOn(NexaApiClient, 'getMyRecordCategories').mockResolvedValue({
        patient_id: 'pat-1',
        categories: [
          {
            category: 'vitals',
            label: 'Vitals',
            icon: '❤️',
            count: 1,
            latest_record_date: '2026-07-17',
            preview: 'BP: 120/80 mmHg',
          },
        ],
      })

      const categorySpy = vi
        .spyOn(NexaApiClient, 'getMyRecordsByCategory')
        .mockRejectedValueOnce(new Error('Failed to load vitals'))
        .mockResolvedValueOnce({
          patient_id: 'pat-1',
          category: 'vitals',
          records: [
            {
              record_id: 'vital-1',
              category: 'vitals',
              type: 'BP',
              value: '120/80',
              unit: 'mmHg',
              recorded_at: '2026-07-17T10:00:00Z',
              source: 'manual',
            },
          ],
          next_cursor: null,
        })

      renderWithTamagui(<PatientRecordsScreen />)
      fireEvent.click(await screen.findByText('Vitals'))

      expect(await screen.findByText('Failed to load vitals')).toBeTruthy()
      const retryBtn = screen.getByText('Retry')
      fireEvent.click(retryBtn)

      await waitFor(() => {
        expect(categorySpy).toHaveBeenCalledTimes(2)
      })
      expect(await screen.findByText('BP')).toBeTruthy()
    })

    it('resets pagination cursor and records state when switching categories, even if next category fails', async () => {
      vi.spyOn(NexaApiClient, 'getMyRecordCategories').mockResolvedValue({
        patient_id: 'pat-1',
        categories: [
          {
            category: 'vitals',
            label: 'Vitals',
            icon: '❤️',
            count: 2,
            latest_record_date: '2026-07-17',
            preview: 'BP: 120/80 mmHg',
          },
          {
            category: 'medications',
            label: 'Medications',
            icon: '💊',
            count: 1,
            latest_record_date: '2026-07-10',
            preview: 'Metformin 500mg',
          },
        ],
      })

      vi.spyOn(NexaApiClient, 'getMyRecordsByCategory')
        .mockResolvedValueOnce({
          patient_id: 'pat-1',
          category: 'vitals',
          records: [
            {
              record_id: 'vital-1',
              category: 'vitals',
              type: 'BP',
              value: '120/80',
              unit: 'mmHg',
              recorded_at: '2026-07-17T10:00:00Z',
              source: 'manual',
            },
          ],
          next_cursor: 'vitals-cursor-page-2',
        })
        .mockRejectedValueOnce(new Error('Network failure loading medications'))

      renderWithTamagui(<PatientRecordsScreen />)

      // Drill down into Vitals
      fireEvent.click(await screen.findByText('Vitals'))
      expect(await screen.findByText('BP')).toBeTruthy()
      expect(screen.getByText('Load older records')).toBeTruthy()

      // Go back to overview
      fireEvent.click(screen.getByText('← All Categories'))
      expect(await screen.findByText('Medications')).toBeTruthy()

      // Drill down into Medications (which fails)
      fireEvent.click(screen.getByText('Medications'))

      expect(await screen.findByText('Network failure loading medications')).toBeTruthy()
      // Previous category records and "Load older records" button must be absent
      expect(screen.queryByText('BP')).toBeNull()
      expect(screen.queryByText('Load older records')).toBeNull()
    })

    it('discards stale category records response when switching categories concurrently (newest-request-wins)', async () => {
      function createDeferred<T>() {
        let resolve!: (val: T) => void
        let reject!: (err: any) => void
        const promise = new Promise<T>((res, rej) => {
          resolve = res
          reject = rej
        })
        return { promise, resolve, reject }
      }

      const deferredVitals = createDeferred<any>()
      const deferredMeds = createDeferred<any>()

      vi.spyOn(NexaApiClient, 'getMyRecordCategories').mockResolvedValue({
        patient_id: 'pat-1',
        categories: [
          {
            category: 'vitals',
            label: 'Vitals',
            icon: '❤️',
            count: 1,
            latest_record_date: '2026-07-17',
            preview: 'BP: 120/80 mmHg',
          },
          {
            category: 'medications',
            label: 'Medications',
            icon: '💊',
            count: 1,
            latest_record_date: '2026-07-10',
            preview: 'Metformin 500mg',
          },
        ],
      })

      vi.spyOn(NexaApiClient, 'getMyRecordsByCategory').mockImplementation((cat: string) => {
        if (cat === 'vitals') return deferredVitals.promise
        if (cat === 'medications') return deferredMeds.promise
        return Promise.resolve({ patient_id: 'pat-1', category: cat, records: [], next_cursor: null } as any)
      })

      renderWithTamagui(<PatientRecordsScreen />)

      // Start Request A: click Vitals
      fireEvent.click(await screen.findByText('Vitals'))
      // Return to overview immediately and click Medications (Request B)
      fireEvent.click(await screen.findByText('← All Categories'))
      fireEvent.click(await screen.findByText('Medications'))

      // Resolve Request B first
      deferredMeds.resolve({
        patient_id: 'pat-1',
        category: 'medications',
        records: [
          {
            record_id: 'med-1',
            category: 'medications',
            name: 'Metformin',
            strength: '500mg',
            recorded_at: '2026-07-10T10:00:00Z',
            source: 'manual',
          },
        ],
        next_cursor: 'med-page-2',
      })

      expect(await screen.findByText('Metformin')).toBeTruthy()
      expect(screen.getByText('Load older records')).toBeTruthy()

      // Resolve Request A later (stale vitals response)
      deferredVitals.resolve({
        patient_id: 'pat-1',
        category: 'vitals',
        records: [
          {
            record_id: 'vital-stale',
            category: 'vitals',
            type: 'Stale BP',
            value: '130/85',
            unit: 'mmHg',
            recorded_at: '2026-07-17T10:00:00Z',
            source: 'manual',
          },
        ],
        next_cursor: 'vitals-stale-cursor',
      })

      // UI must remain showing Medications, never showing Stale BP
      await waitFor(() => {
        expect(screen.queryByText('Stale BP')).toBeNull()
        expect(screen.getByText('Metformin')).toBeTruthy()
      })
    })
  })

  describe('PatientPrescriptionsScreen', () => {
    it('renders prescriptions list with honest provenance badges', async () => {
      vi.spyOn(NexaApiClient, 'getMyPrescriptions').mockResolvedValue({
        patient_id: 'pat-1',
        prescriptions: [
          {
            prescription_id: 'rx-1',
            medication_name: 'Atorvastatin',
            strength: '20mg',
            frequency: 'Once nightly',
            prescribed_at: '2026-05-15T00:00:00Z',
            source: 'manual',
            source_display: 'Clinician Prescribed',
            risk_level: 'LOW_RISK',
            has_source_document: true,
          },
        ],
        next_cursor: null,
      })

      renderWithTamagui(<PatientPrescriptionsScreen />)

      expect(await screen.findByText('Atorvastatin')).toBeTruthy()
      expect(screen.getByText('20mg • Once nightly')).toBeTruthy()
      expect(screen.getByText('Clinician Prescribed')).toBeTruthy()
    })

    it('filters prescriptions by source and search query with honest empty state', async () => {
      vi.spyOn(NexaApiClient, 'getMyPrescriptions').mockResolvedValue({
        patient_id: 'pat-1',
        prescriptions: [
          {
            prescription_id: 'rx-1',
            medication_name: 'Atorvastatin',
            strength: '20mg',
            frequency: 'Once nightly',
            prescribed_at: '2026-05-15T00:00:00Z',
            source: 'manual',
            source_display: 'Clinician Prescribed',
            risk_level: 'LOW_RISK',
            has_source_document: true,
            is_external_document: false,
          },
          {
            prescription_id: 'rx-2',
            medication_name: 'Amoxicillin',
            strength: '500mg',
            frequency: 'Three times daily',
            prescribed_at: '2026-06-01T00:00:00Z',
            source: 'patient_uploaded',
            source_display: 'Uploaded Prescription',
            risk_level: 'LOW_RISK',
            has_source_document: true,
            is_external_document: true,
          },
        ],
        next_cursor: null,
      })

      renderWithTamagui(<PatientPrescriptionsScreen />)

      expect(await screen.findByText('Atorvastatin')).toBeTruthy()
      expect(screen.getByText('Amoxicillin')).toBeTruthy()
      expect(screen.getByText('✓ All prescriptions loaded')).toBeTruthy()

      // Filter by Clinic Prescriptions
      fireEvent.click(screen.getByText('Clinic Prescriptions'))
      expect(screen.getByText('Atorvastatin')).toBeTruthy()
      expect(screen.queryByText('Amoxicillin')).toBeNull()

      // Filter by Uploaded Prescriptions
      fireEvent.click(screen.getByText('Uploaded Prescriptions'))
      expect(screen.queryByText('Atorvastatin')).toBeNull()
      expect(screen.getByText('Amoxicillin')).toBeTruthy()

      // Search query
      const searchInput = screen.getByPlaceholderText('Search prescriptions or medications…')
      fireEvent.change(searchInput, { target: { value: 'Zithromax' } })
      expect(await screen.findByText('No prescriptions match your filter.')).toBeTruthy()

      // Clear filters
      fireEvent.click(screen.getByText('Reset Filters'))
      expect(await screen.findByText('Atorvastatin')).toBeTruthy()
      expect(screen.getByText('Amoxicillin')).toBeTruthy()
    })

    it('supports retrying prescription loading on failure', async () => {
      const rxSpy = vi
        .spyOn(NexaApiClient, 'getMyPrescriptions')
        .mockRejectedValueOnce(new Error('Network error on prescriptions'))
        .mockResolvedValueOnce({
          patient_id: 'pat-1',
          prescriptions: [],
          next_cursor: null,
        })

      renderWithTamagui(<PatientPrescriptionsScreen />)

      expect(await screen.findByText('Network error on prescriptions')).toBeTruthy()
      const retryBtn = screen.getByText('Retry')
      fireEvent.click(retryBtn)

      await waitFor(() => {
        expect(rxSpy).toHaveBeenCalledTimes(2)
      })
      expect(await screen.findByText('No active or historical medications on file.')).toBeTruthy()
    })

    it('has exactly one search input surface supporting medication name, strength, and frequency', async () => {
      vi.spyOn(NexaApiClient, 'getMyPrescriptions').mockResolvedValue({
        patient_id: 'pat-1',
        prescriptions: [
          {
            prescription_id: 'rx-1',
            medication_name: 'Metformin',
            strength: '500mg',
            frequency: 'Twice daily',
            prescribed_at: '2026-05-15T00:00:00Z',
            source: 'manual',
            source_display: 'Clinician Prescribed',
            risk_level: 'LOW_RISK',
            has_source_document: true,
            is_external_document: false,
          },
        ],
        next_cursor: null,
      })

      renderWithTamagui(<PatientPrescriptionsScreen />)

      expect(await screen.findByText('Metformin')).toBeTruthy()
      const searchInputs = screen.getAllByPlaceholderText(/Search prescriptions/i)
      expect(searchInputs).toHaveLength(1)
      expect(screen.queryByPlaceholderText('Search medication name or dosage…')).toBeNull()

      // Search by frequency
      fireEvent.change(searchInputs[0], { target: { value: 'twice daily' } })
      expect(screen.getByText('Metformin')).toBeTruthy()

      // Search by strength
      fireEvent.change(searchInputs[0], { target: { value: '500mg' } })
      expect(screen.getByText('Metformin')).toBeTruthy()

      // Search mismatch
      fireEvent.change(searchInputs[0], { target: { value: 'Insulin' } })
      expect(await screen.findByText('No prescriptions match your filter.')).toBeTruthy()
    })
  })

  describe('PatientReportsScreen', () => {
    it('renders reports list with document types and safe provenance', async () => {
      vi.spyOn(NexaApiClient, 'getMyReports').mockResolvedValue({
        patient_id: 'pat-1',
        reports: [
          {
            report_id: 'doc-1',
            report_title: 'Discharge Summary (Inpatient)',
            document_type: 'DISCHARGE_SUMMARY',
            uploaded_at: '2026-06-01T08:00:00Z',
            source: 'patient_uploaded',
            source_display: 'Patient Uploaded / External Source',
            can_view_source: true,
          },
        ],
        next_cursor: null,
      })

      renderWithTamagui(<PatientReportsScreen />)

      expect(await screen.findByText('Discharge Summary (Inpatient)')).toBeTruthy()
      expect(screen.getByText('Type: DISCHARGE_SUMMARY')).toBeTruthy()
      expect(screen.getByText('Patient Uploaded / External Source')).toBeTruthy()
      expect(screen.getByText('All Reports')).toBeTruthy()
      expect(screen.getByText('Labs')).toBeTruthy()
      expect(screen.getByText('Imaging')).toBeTruthy()
    })

    it('filters reports when selecting filter tabs', async () => {
      const getReportsSpy = vi.spyOn(NexaApiClient, 'getMyReports').mockResolvedValue({
        patient_id: 'pat-1',
        reports: [],
        next_cursor: null,
      })

      renderWithTamagui(<PatientReportsScreen />)

      const labsTab = await screen.findByText('Labs')
      fireEvent.click(labsTab)

      await waitFor(() => {
        expect(getReportsSpy).toHaveBeenCalledWith(
          expect.objectContaining({
            documentType: 'lab_report',
          })
        )
      })
    })

    it('renders differentiated tab empty state with reset button', async () => {
      vi.spyOn(NexaApiClient, 'getMyReports').mockResolvedValue({
        patient_id: 'pat-1',
        reports: [],
        next_cursor: null,
      })

      renderWithTamagui(<PatientReportsScreen />)

      // Click Imaging
      fireEvent.click(await screen.findByText('Imaging'))
      expect(await screen.findByText('No Imaging reports on file.')).toBeTruthy()
      expect(
        screen.getByText('There are no documents matching this filter in your health record.')
      ).toBeTruthy()

      // Click Show All Reports
      fireEvent.click(screen.getByText('Show All Reports'))
      expect(await screen.findByText('No diagnostic reports or documents on file.')).toBeTruthy()
    })

    it('supports retrying reports loading on failure', async () => {
      const reportsSpy = vi
        .spyOn(NexaApiClient, 'getMyReports')
        .mockRejectedValueOnce(new Error('Network error on reports'))
        .mockResolvedValueOnce({
          patient_id: 'pat-1',
          reports: [],
          next_cursor: null,
        })

      renderWithTamagui(<PatientReportsScreen />)

      expect(await screen.findByText('Network error on reports')).toBeTruthy()
      const retryBtn = screen.getByText('Retry')
      fireEvent.click(retryBtn)

      await waitFor(() => {
        expect(reportsSpy).toHaveBeenCalledTimes(2)
      })
      expect(await screen.findByText('No diagnostic reports or documents on file.')).toBeTruthy()
    })

    it('discards stale reports response when switching filter tabs concurrently (newest-request-wins)', async () => {
      function createDeferred<T>() {
        let resolve!: (val: T) => void
        let reject!: (err: any) => void
        const promise = new Promise<T>((res, rej) => {
          resolve = res
          reject = rej
        })
        return { promise, resolve, reject }
      }

      const deferredLabs = createDeferred<any>()
      const deferredImaging = createDeferred<any>()

      vi.spyOn(NexaApiClient, 'getMyReports')
        .mockResolvedValueOnce({
          patient_id: 'pat-1',
          reports: [],
          next_cursor: null,
        })
        .mockImplementation((params: any) => {
          if (params?.documentType === 'lab_report') return deferredLabs.promise
          if (params?.documentType === 'imaging_report') return deferredImaging.promise
          return Promise.resolve({ patient_id: 'pat-1', reports: [], next_cursor: null } as any)
        })

      renderWithTamagui(<PatientReportsScreen />)

      expect(await screen.findByText('All Reports')).toBeTruthy()

      // Switch to Labs (Request A)
      fireEvent.click(screen.getByText('Labs'))
      // Switch immediately to Imaging (Request B)
      fireEvent.click(screen.getByText('Imaging'))

      const imagingReport = {
        report_id: 'rep-imaging-1',
        report_title: 'Chest X-Ray PA View',
        document_type: 'imaging_report',
        uploaded_at: '2026-06-01T08:00:00Z',
        source: 'manual',
        source_display: 'Radiology Department',
        can_view_source: false,
      }

      const labsReport = {
        report_id: 'rep-labs-stale',
        report_title: 'Stale Lipid Panel',
        document_type: 'lab_report',
        uploaded_at: '2026-06-01T08:00:00Z',
        source: 'ai_extracted',
        source_display: 'Pathology Lab',
        can_view_source: false,
      }

      // Resolve Imaging first
      deferredImaging.resolve({
        patient_id: 'pat-1',
        reports: [imagingReport],
        next_cursor: null,
      })

      expect(await screen.findByText('Chest X-Ray PA View')).toBeTruthy()

      // Resolve Labs later
      deferredLabs.resolve({
        patient_id: 'pat-1',
        reports: [labsReport],
        next_cursor: null,
      })

      // UI must remain showing Imaging, never showing Stale Lipid Panel
      await waitFor(() => {
        expect(screen.queryByText('Stale Lipid Panel')).toBeNull()
        expect(screen.getByText('Chest X-Ray PA View')).toBeTruthy()
      })
    })
  })

  describe('PatientRecordDetailModal', () => {
    it('renders structured clinical observations and provenance card', async () => {
      vi.spyOn(NexaApiClient, 'getMyRecordDetail').mockResolvedValue({
        record_id: 'rec-1',
        patient_id: 'pat-1',
        category: 'vitals',
        title: 'Vitals Observation: BP',
        fields: {
          type: 'BP',
          value: '120/80',
          unit: 'mmHg',
        },
        recorded_at: '2026-07-17T10:00:00Z',
        provenance: {
          source: 'manual',
          source_display: 'Clinician Recorded',
          confidence: null,
          risk_level: 'LOW_RISK',
          has_source_document: false,
        },
      })

      renderWithTamagui(
        <PatientRecordDetailModal
          open={true}
          onOpenChange={vi.fn()}
          category="vitals"
          recordId="rec-1"
        />
      )

      expect(await screen.findByText('Vitals Observation: BP')).toBeTruthy()
      expect(screen.getByText('Clinical Data')).toBeTruthy()
      expect(screen.getByText('Provenance & Clinical Trust')).toBeTruthy()
      expect(screen.getByText('Origin: Clinician Recorded')).toBeTruthy()
    })

    it('dismisses on Escape key press on web', async () => {
      const onOpenChange = vi.fn()
      vi.spyOn(NexaApiClient, 'getMyRecordDetail').mockResolvedValue({
        record_id: 'rec-1',
        patient_id: 'pat-1',
        category: 'vitals',
        title: 'BP Observation',
        fields: {},
        recorded_at: null,
        provenance: { source: 'manual' },
      })

      renderWithTamagui(
        <PatientRecordDetailModal
          open={true}
          onOpenChange={onOpenChange}
          category="vitals"
          recordId="rec-1"
        />
      )

      expect(await screen.findByText('BP Observation')).toBeTruthy()

      fireEvent.keyDown(window, { key: 'Escape', code: 'Escape' })
      expect(onOpenChange).toHaveBeenCalledWith(false)
    })
  })
})
