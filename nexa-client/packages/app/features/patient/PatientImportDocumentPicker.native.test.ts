import { beforeEach, describe, expect, it, vi } from 'vitest'

const { getDocumentAsync } = vi.hoisted(() => ({
  getDocumentAsync: vi.fn(),
}))

vi.mock('expo-document-picker', () => ({ getDocumentAsync }))

import { pickNativePatientImportDocument } from './PatientImportDocumentPicker.native'

describe('PatientImportDocumentPicker native adapter', () => {
  beforeEach(() => {
    getDocumentAsync.mockReset()
  })

  it('retains native DocumentPicker selection for Android and iOS', async () => {
    getDocumentAsync.mockResolvedValue({
      canceled: false,
      assets: [
        {
          name: 'native-report.pdf',
          mimeType: 'application/pdf',
          size: 2048,
          uri: 'file:///cache/native-report.pdf',
        },
      ],
    })

    await expect(
      pickNativePatientImportDocument(['application/pdf'])
    ).resolves.toMatchObject({
      name: 'native-report.pdf',
      type: 'application/pdf',
      size: 2048,
      uri: 'file:///cache/native-report.pdf',
    })
    expect(getDocumentAsync).toHaveBeenCalledWith({
      type: ['application/pdf'],
      copyToCacheDirectory: true,
    })
  })

  it('does not select a document when the native picker is cancelled', async () => {
    getDocumentAsync.mockResolvedValue({ canceled: true, assets: null })

    await expect(
      pickNativePatientImportDocument(['application/pdf'])
    ).resolves.toBeNull()
  })
})
