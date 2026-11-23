import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const login = readFileSync('nexa-client/apps/next/app/patient/login/page.tsx', 'utf8')
const demo = readFileSync('nexa-client/apps/next/app/demo/patient/page.tsx', 'utf8')
const guard = readFileSync('app/security/staging_otp_guard.py', 'utf8')

test('a failed API deployment cannot erase the patient entry workflow', () => {
  assert.match(login, /label="Mobile Number"/)
  assert.match(login, /Send Verification Code/)
  assert.match(login, /Verify & Open Dashboard/)
  assert.match(login, /requestPatientOtp\(phone\)/)
  assert.match(login, /verifyPatientOtp\(phone, otp\)/)
  assert.doesNotMatch(login, /\{!STAGING_READ_ONLY\s*&&\s*\(\s*<>/)
})

test('staging preview has a prominent usable patient workflow route', () => {
  assert.match(login, /aria-label="Explore synthetic patient workflow"/)
  assert.match(login, /inert=\\{STAGING_READ_ONLY/)
  assert.match(login, /fieldset disabled=\\{STAGING_READ_ONLY\\}/)
  assert.ok(
    login.indexOf('aria-label="Explore synthetic patient workflow"') < login.indexOf('<div inert='),
    'the patient walkthrough button must remain outside the disabled OTP form',
  )
  assert.match(login, /router\.push\('\/demo\/patient'\)/)
  assert.match(login, /Staging preview only/)
  assert.match(login, /if \(STAGING_READ_ONLY\)/)
  assert.match(login, /disabled=\{STAGING_READ_ONLY \|\| loading/)
  assert.match(demo, /NEXT_PUBLIC_STAGING_READ_ONLY === '1'/)
  for (const page of ['Overview', 'Records', 'Timeline', 'Consent']) {
    assert.ok(demo.includes(page), 'missing demo step: ' + page)
  }
})

test('the synthetic walkthrough cannot become an auth or clinical API shortcut', () => {
  assert.doesNotMatch(demo, /\bfetch\s*\(|\baxios\b|\bapiClient\b|\bsupabase\b|localStorage|sessionStorage|document\.cookie/i)
  assert.doesNotMatch(demo, /storePatientAuthSession|Authorization|access_token|device_enrollment_token/)
  assert.match(demo, /NO network requests/)
  assert.match(demo, /NOT A PATIENT SESSION/)
  assert.match(guard, /STAGING_OTP_PHONE_ALLOWLIST/)
})
