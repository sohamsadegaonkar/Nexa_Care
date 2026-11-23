import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const login = readFileSync('nexa-client/apps/next/app/patient/login/page.tsx', 'utf8')
const demo = readFileSync('nexa-client/apps/next/app/demo/patient/page.tsx', 'utf8')
const guard = readFileSync('app/security/staging_otp_guard.py', 'utf8')

test('real authenticated patient form and handlers survive the staging fix', () => {
  assert.match(login, /label="Mobile Number"/)
  assert.match(login, /Send Verification Code/)
  assert.match(login, /Verify & Open Dashboard/)
  assert.match(login, /requestPatientOtp\(phone\)/)
  assert.match(login, /verifyPatientOtp\(phone, otp\)/)
  assert.match(login, /STAGING_READ_ONLY \? \(/)
  assert.match(login, /if \(STAGING_READ_ONLY\)/)
})

test('staging entry has an editable fictional-code form, not a dead phone button', () => {
  assert.ok(login.includes('aria-label="Synthetic patient entry"'))
  assert.ok(login.includes('id="synthetic-patient-code"'))
  assert.ok(login.includes('onChange={(event) => {'))
  assert.ok(login.includes('event.preventDefault()'))
  assert.ok(login.includes("router.push('/demo/patient')"))
  assert.ok(login.includes('Open interactive patient sandbox'))
  assert.ok(login.includes("Real phone numbers and patient identifiers are not accepted"))
  assert.ok(!login.includes('<div inert={STAGING_READ_ONLY'))
  assert.ok(!login.includes('<fieldset disabled={STAGING_READ_ONLY'))
  assert.ok(login.includes('/^DEMO-[A-Z0-9]{3,8}$/.test(candidate)'))
})

test('fictional report creation is interactive and updates the dashboard and timeline', () => {
  assert.ok(demo.includes('Create fictional demo report'))
  assert.ok(demo.includes('Add fictional report'))
  assert.ok(demo.includes('setSampleRecords((records) => [...records, candidate])'))
  assert.ok(demo.includes('String(sampleRecords.length)'))
  assert.ok(demo.includes('sampleRecords.slice(1).map'))
  for (const page of ['Overview', 'Records', 'Timeline', 'Consent']) {
    assert.ok(demo.includes(page), 'missing demo step: ' + page)
  }
})

test('synthetic walkthrough cannot become an auth or clinical API shortcut', () => {
  assert.match(demo, /NEXT_PUBLIC_STAGING_READ_ONLY === '1'/)
  assert.doesNotMatch(demo, /\bfetch\s*\(|\baxios\b|\bapiClient\b|\bsupabase\b|localStorage|sessionStorage|document\.cookie/i)
  assert.doesNotMatch(demo, /storePatientAuthSession|Authorization|access_token|device_enrollment_token/)
  assert.match(demo, /NO network requests/)
  assert.match(demo, /NOT A PATIENT SESSION/)
  assert.match(guard, /STAGING_OTP_PHONE_ALLOWLIST/)
})
