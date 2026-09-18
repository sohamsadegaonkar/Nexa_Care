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
  })
})
