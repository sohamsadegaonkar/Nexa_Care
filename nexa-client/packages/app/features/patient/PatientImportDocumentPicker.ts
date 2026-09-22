// TypeScript does not apply React Native platform suffix resolution during the
// Next.js type-check. Metro selects `.native.ts` on device; web tooling uses
// this safe fallback (or the explicit `.web.ts` resolver entry).
export { pickNativePatientImportDocument } from './PatientImportDocumentPicker.web'
