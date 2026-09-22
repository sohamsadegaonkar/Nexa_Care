import * as DocumentPicker from 'expo-document-picker'
import type { SelectedSourceFile } from '../../utils/apiClient'

/** Native-only bridge. Metro selects this file for Android and iOS. */
export async function pickNativePatientImportDocument(
  types: string[]
): Promise<SelectedSourceFile | null> {
  const result = await DocumentPicker.getDocumentAsync({
    type: types,
    copyToCacheDirectory: true,
  })

  if (result.canceled || !result.assets || result.assets.length === 0) {
    return null
  }

  const asset = result.assets[0]
  return {
    name: asset.name,
    type: asset.mimeType || 'application/octet-stream',
    size: asset.size || 0,
    uri: asset.uri,
    file: asset.file,
  }
}
