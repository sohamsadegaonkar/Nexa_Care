import type { SelectedSourceFile } from '../../utils/apiClient'

/** Web uses the browser file input in PatientImportScreen and never loads Expo's native picker. */
export async function pickNativePatientImportDocument(
  _types: string[]
): Promise<SelectedSourceFile | null> {
  throw new Error('Native document selection is unavailable in this browser.')
}
