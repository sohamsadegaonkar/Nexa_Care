import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, existsSync } from 'node:fs'

const path = 'nexa-client/apps/next/app/patient/login/page.tsx'
const login = readFileSync(path, 'utf8')
const root = readFileSync('nexa-client/apps/next/app/page.tsx', 'utf8')

test('patient portal retains original real phone OTP, verification and dashboard navigation', () => {
  assert.match(login, /label="Mobile Number"/)
  assert.match(login, /Send Verification Code/)
  assert.match(login, /Verify & Open Dashboard/)
  assert.match(login, /requestPatientOtp\(phone\)/)
  assert.match(login, /verifyPatientOtp\(phone, otp\)/)
  assert.match(login, /storePatientAuthSession\(data.access_token, data.device_enrollment_token\)/)
  assert.match(login, /router.push\('\/patient\/dashboard'\)/)
})

test('do not replace original screens with demo or preview-only login', () => {
  assert.doesNotMatch(login, /STAGING_READ_ONLY|DEMO-001|Synthetic patient code|Fictional demo/)
  assert.doesNotMatch(root, /STAGING_WEB_PREVIEW|backend login and clinical workflows are not connected yet/)
  assert.equal(existsSync('nexa-client/apps/next/app/demo/patient/page.tsx'), false)
})

test('original provider entry remains linked from the patient login', () => {
  assert.match(login, /router.push\('\/doctor\/login'\)/)
  assert.match(root, /DoctorLoginScreen showEntryOptions/)
})
