import React from 'react'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithTamagui } from '../../../../test/test-utils'
import {
  NexaApiClient,
  type PatientExternalRecordResponse,
  type PatientExternalRecordUploadPolicy,
} from '../../utils/apiClient'
import PatientOnboardingCard from './PatientOnboardingCard'
import PatientOnboardingScreen from './PatientOnboardingScreen'
import PatientImportScreen, {
  resolveReturnDestination,
} from './PatientImportScreen'

const { push } = vi.hoisted(() => ({ push: vi.fn() }))

vi.mock('expo-router', () => ({
  useRouter: () => ({ push }),
}))

vi.mock('solito/navigation', () => ({
  useRouter: () => ({ push }),
}))

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push }),
  usePathname: () => '/patient/onboarding',
  useSearchParams: () => new URLSearchParams(),
}))

vi.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, right: 0, bottom: 8, left: 0 }),
}))

const mockPolicy: PatientExternalRecordUploadPolicy = {
  max_upload_bytes: 10 * 1024 * 1024,
  accepted_extensions: ['.pdf', '.png', '.jpg', '.jpeg'],
  accepted_mime_types: ['application/pdf', 'image/png', 'image/jpeg'],
}

const mockRetryableResponse: PatientExternalRecordResponse = {
  import_id: 'imp-retry-123',
  category: 'prescription',
  status: 'failed_retryable',
  actions: {
    can_process: false,
    can_retry: true,
    can_cancel: true,
    can_review: false,
    can_save: false,
    can_view_source: false,
  },
  failure_reason: 'Temporary scanner timeout',
}

const mockCancelledResponse: PatientExternalRecordResponse = {
  import_id: 'imp-cancel-123',
  category: 'prescription',
  status: 'cancelled',
  actions: {
    can_process: false,
    can_retry: false,
    can_cancel: false,
    can_review: false,
    can_save: false,
    can_view_source: false,
  },
}

const mockCompletedResponse: PatientExternalRecordResponse = {
  import_id: 'imp-done-123',
  category: 'prescription',
  status: 'imported',
  actions: {
    can_process: false,
    can_retry: false,
    can_cancel: false,
    can_review: false,
    can_save: false,
    can_view_source: true,
  },
}

describe('Slice 11F: Patient Onboarding External Record Import Integration', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(NexaApiClient, 'getPatientUploadPolicy').mockResolvedValue(mockPolicy)
  })

  // ── 1. resolveReturnDestination Contract ────────────────────────────────
  describe('resolveReturnDestination', () => {
    it('resolves "onboarding" only for exact matching string', () => {
      expect(resolveReturnDestination('onboarding')).toBe('onboarding')
    })

    it('defaults to "records" for null, undefined, or empty string', () => {
      expect(resolveReturnDestination(null)).toBe('records')
      expect(resolveReturnDestination(undefined)).toBe('records')
      expect(resolveReturnDestination('')).toBe('records')
    })

    it('defaults to "records" for explicit records destination', () => {
      expect(resolveReturnDestination('records')).toBe('records')
    })

    it('strictly rejects arbitrary or malicious redirect URLs and falls back to "records"', () => {
      expect(resolveReturnDestination('https://malicious-site.com')).toBe('records')
      expect(resolveReturnDestination('//attacker.org/phish')).toBe('records')
      expect(resolveReturnDestination('javascript:alert(1)')).toBe('records')
      expect(resolveReturnDestination('/patient/profile')).toBe('records')
      expect(resolveReturnDestination('ONBOARDING')).toBe('records')
    })
  })

  // ── 2. PatientOnboardingCard Unit & Accessibility ───────────────────────
  describe('PatientOnboardingCard', () => {
    it('renders with non-coercive copy, "OPTIONAL" badge, and accessible region', () => {
      renderWithTamagui(<PatientOnboardingCard />)

      // Heading and badge
      expect(
        screen.getByText('Do you have previous medical records?')
      ).toBeDefined()
      expect(screen.getByText('OPTIONAL')).toBeDefined()

      // Non-coercive explanatory text
      expect(
        screen.getByText(/This step is entirely optional/i)
      ).toBeDefined()

      // Buttons
      expect(screen.getByText('Add Medical Record →')).toBeDefined()
      expect(screen.getByText('Skip for now')).toBeDefined()
    })

    it('navigates to /patient/records/import?returnTo=onboarding on "Add Medical Record →"', () => {
      renderWithTamagui(<PatientOnboardingCard />)

      const addBtn = screen.getByRole('button', { name: /Add Medical Record/i })
      fireEvent.click(addBtn)

      expect(push).toHaveBeenCalledWith('/patient/records/import?returnTo=onboarding')
    })

    it('navigates to /patient/dashboard on "Skip for now"', () => {
      renderWithTamagui(<PatientOnboardingCard />)

      const skipBtn = screen.getByRole('button', {
        name: /Skip adding records for now/i,
      })
      fireEvent.click(skipBtn)

      expect(push).toHaveBeenCalledWith('/patient/dashboard')
    })

    it('invokes custom onAddRecord and onSkip callbacks when provided', () => {
      const onAddRecord = vi.fn()
      const onSkip = vi.fn()

      renderWithTamagui(
        <PatientOnboardingCard onAddRecord={onAddRecord} onSkip={onSkip} />
      )

      fireEvent.click(
        screen.getByRole('button', { name: /Add Medical Record/i })
      )
      expect(onAddRecord).toHaveBeenCalledTimes(1)
      expect(push).not.toHaveBeenCalled()

      fireEvent.click(
        screen.getByRole('button', { name: /Skip adding records for now/i })
      )
      expect(onSkip).toHaveBeenCalledTimes(1)
    })
  })

  // ── 3. PatientOnboardingScreen ──────────────────────────────────────────
  describe('PatientOnboardingScreen', () => {
    it('renders onboarding overview and embedded PatientOnboardingCard', () => {
      renderWithTamagui(<PatientOnboardingScreen />)

      expect(screen.getByText('Welcome to Nexa Care')).toBeDefined()
      expect(screen.getByText('Account & Privacy Ready')).toBeDefined()
      expect(
        screen.getByText('Do you have previous medical records?')
      ).toBeDefined()
      expect(screen.getByText('OPTIONAL')).toBeDefined()
    })
  })

  // ── 4. PatientImportScreen with returnTo="onboarding" ───────────────────
  describe('PatientImportScreen with returnTo="onboarding"', () => {
    it('renders "← Onboarding" header button and navigates to /patient/onboarding', () => {
      renderWithTamagui(<PatientImportScreen returnTo="onboarding" />)

      const backBtn = screen.getByRole('button', {
        name: /Back to Onboarding/i,
      })
      expect(backBtn).toBeDefined()
      expect(screen.getByText('← Onboarding')).toBeDefined()

      fireEvent.click(backBtn)
      expect(push).toHaveBeenCalledWith('/patient/onboarding')
    })

    it('invokes onReturn callback when provided', () => {
      const onReturn = vi.fn()
      renderWithTamagui(
        <PatientImportScreen returnTo="onboarding" onReturn={onReturn} />
      )

      const backBtn = screen.getByRole('button', {
        name: /Back to Onboarding/i,
      })
      fireEvent.click(backBtn)
      expect(onReturn).toHaveBeenCalledWith('onboarding')
      expect(push).not.toHaveBeenCalled()
    })

    it('renders "Continue onboarding →" in Step 4 when import completes', async () => {
      vi.spyOn(NexaApiClient, 'getPatientExternalRecord').mockResolvedValue(
        mockCompletedResponse
      )

      renderWithTamagui(
        <PatientImportScreen
          initialImportId="imp-done-123"
          returnTo="onboarding"
        />
      )

      await waitFor(() => {
        expect(screen.getByText('Document Imported')).toBeDefined()
      })

      const continueBtn = screen.getByRole('button', {
        name: /Continue onboarding/i,
      })
      expect(continueBtn).toBeDefined()

      // Also verifies "+ Add Another Document" and "View in Medical Records" are available
      expect(
        screen.getByRole('button', { name: /Add Another Document/i })
      ).toBeDefined()
      expect(
        screen.getByRole('button', { name: /View in Medical Records/i })
      ).toBeDefined()

      fireEvent.click(continueBtn)
      expect(push).toHaveBeenCalledWith('/patient/onboarding')
    })

    it('renders non-blocking skip affordance in Step 2 when extraction is retryable', async () => {
      vi.spyOn(NexaApiClient, 'getPatientExternalRecord').mockResolvedValue(
        mockRetryableResponse
      )

      renderWithTamagui(
        <PatientImportScreen
          initialImportId="imp-retry-123"
          returnTo="onboarding"
        />
      )

      await waitFor(() => {
        expect(screen.getByText('Extraction Paused')).toBeDefined()
      })

      const skipBtn = screen.getByRole('button', {
        name: /Skip for now/i,
      })
      expect(skipBtn).toBeDefined()

      fireEvent.click(skipBtn)
      expect(push).toHaveBeenCalledWith('/patient/onboarding')
    })

    it('renders non-blocking "Continue onboarding →" when import is cancelled', async () => {
      vi.spyOn(NexaApiClient, 'getPatientExternalRecord').mockResolvedValue(
        mockCancelledResponse
      )

      renderWithTamagui(
        <PatientImportScreen
          initialImportId="imp-cancel-123"
          returnTo="onboarding"
        />
      )

      await waitFor(() => {
        expect(screen.getByText('Import Cancelled')).toBeDefined()
      })

      const continueBtn = screen.getByRole('button', {
        name: /Continue onboarding/i,
      })
      expect(continueBtn).toBeDefined()

      fireEvent.click(continueBtn)
      expect(push).toHaveBeenCalledWith('/patient/onboarding')
    })
  })

  // ── 5. PatientImportScreen with default/records destination ─────────────
  describe('PatientImportScreen with default/records destination', () => {
    it('renders "← Medical Records" header button and navigates to /patient/records', () => {
      renderWithTamagui(<PatientImportScreen />)

      const backBtn = screen.getByRole('button', {
        name: /Back to Medical Records/i,
      })
      expect(backBtn).toBeDefined()
      expect(screen.getByText('← Medical Records')).toBeDefined()

      fireEvent.click(backBtn)
      expect(push).toHaveBeenCalledWith('/patient/records')
    })

    it('renders "View in Medical Records" as primary CTA in Step 4', async () => {
      vi.spyOn(NexaApiClient, 'getPatientExternalRecord').mockResolvedValue(
        mockCompletedResponse
      )

      renderWithTamagui(<PatientImportScreen initialImportId="imp-done-123" />)

      await waitFor(() => {
        expect(screen.getByText('Document Imported')).toBeDefined()
      })

      expect(
        screen.getByRole('button', { name: /View in Medical Records/i })
      ).toBeDefined()
      expect(
        screen.getByRole('button', { name: /View Health Timeline/i })
      ).toBeDefined()
      expect(
        screen.getByRole('button', { name: /Import Another Document/i })
      ).toBeDefined()
    })
  })
})
