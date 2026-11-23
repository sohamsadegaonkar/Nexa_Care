'use client'

import { useState } from 'react'
import type { CSSProperties } from 'react'

// This route is intentionally separate from /patient/* and its authenticated API.
// It uses literal synthetic fixtures, makes NO network requests and never creates
// a session or consent capability. It exists only in the isolated preview build.
const PREVIEW_ENABLED = process.env.NEXT_PUBLIC_STAGING_READ_ONLY === '1'

type View = 'Overview' | 'Records' | 'Timeline' | 'Consent'
type DemoDecision = 'pending' | 'approved' | 'denied' | 'revoked'
const views: View[] = ['Overview', 'Records', 'Timeline', 'Consent']

const panel: CSSProperties = {
  background: '#fff',
  border: '1px solid #d7e2df',
  borderRadius: 16,
  padding: 24,
  marginTop: 20,
  boxShadow: '0 3px 14px rgba(18, 65, 63, .04)',
}
const buttonStyle: CSSProperties = {
  background: '#087a74',
  color: '#fff',
  borderRadius: 10,
  border: '1px solid #087a74',
  padding: '12px 18px',
  fontWeight: 700,
  cursor: 'pointer',
  minHeight: 44,
}
const secondaryButton: CSSProperties = {
  ...buttonStyle,
  color: '#125c58',
  background: '#e9f6f3',
  borderColor: '#b5d9d4',
}
const muted: CSSProperties = { color: '#526a67', lineHeight: 1.6 }

export default function SyntheticPatientWalkthrough() {
  const [view, setView] = useState<View>('Overview')
  const [decision, setDecision] = useState<DemoDecision>('pending')
  const [reportOpen, setReportOpen] = useState(false)
  const [sampleRecords, setSampleRecords] = useState(['Example blood test summary'])
  const [newRecordLabel, setNewRecordLabel] = useState('')
  const [recordError, setRecordError] = useState<string | null>(null)

  if (!PREVIEW_ENABLED) {
    return (
      <main style={{ maxWidth: 760, margin: '80px auto', padding: 24 }}>
        <h1>Patient demo is not enabled</h1>
        <p>This synthetic-only walkthrough is available in the isolated staging preview, not the real patient portal.</p>
        <a href="/patient/login">Return to patient sign in</a>
      </main>
    )
  }

  return (
    <main style={{ maxWidth: 980, margin: '0 auto', padding: '32px 20px 72px', color: '#153b3a' }}>
      <div role="status" style={{
        borderRadius: 12, padding: '14px 18px', background: '#fff0d9',
        border: '1px solid #d99a29', lineHeight: 1.55,
      }}>
        <strong>SYNTHETIC WALKTHROUGH — NOT A PATIENT SESSION</strong>
        <div>No sign-in, SMS, medical records, document bytes, or live consent are accessed. Everything below is fictional and resets on refresh.</div>
      </div>

      <header style={{ marginTop: 28 }}>
        <a href="/patient/login" style={{ color: '#087a74', fontWeight: 700 }}>← Patient sign in</a>
        <p style={{ ...muted, marginBottom: 4 }}>Nexa Care · Patient experience</p>
        <h1 style={{ fontSize: 'clamp(28px, 4vw, 42px)', margin: '4px 0 10px' }}>Explore the patient workflow</h1>
        <p style={muted}>Navigate the example patient dashboard, a sample report, timeline activity and a simulated access decision.</p>
      </header>

      <nav aria-label="Synthetic patient walkthrough" style={{ display: 'flex', gap: 9, flexWrap: 'wrap', marginTop: 24 }}>
        {views.map((entry) => (
          <button
            type="button"
            key={entry}
            onClick={() => setView(entry)}
            aria-current={view === entry ? 'page' : undefined}
            style={view === entry ? buttonStyle : secondaryButton}
          >
            {entry}
          </button>
        ))}
      </nav>

      {view === 'Overview' && (
        <section aria-label="Synthetic dashboard" style={panel}>
          <p style={{ color: '#087a74', fontWeight: 800, margin: '0 0 8px' }}>DEMO PATIENT · NO PERSONAL DATA</p>
          <h2 style={{ marginTop: 0 }}>Your health, your control</h2>
          <p style={muted}>A preview of the patient dashboard without an authenticated session or any clinical API calls.</p>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 14, margin: '24px 0' }}>
            {[[String(sampleRecords.length), 'Sample reports'], [String(sampleRecords.length + 2), 'Example timeline events'], [decision === 'approved' ? '1' : '0', 'Simulated approvals']].map(([value, label]) => (
              <div key={label} style={{ flex: '1 1 160px', padding: 18, background: '#eff7f5', borderRadius: 12 }}>
                <div style={{ fontSize: 30, fontWeight: 800 }}>{value}</div>
                <div style={{ ...muted, fontSize: 14 }}>{label}</div>
              </div>
            ))}
          </div>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            <button type="button" style={buttonStyle} onClick={() => setView('Records')}>View sample records</button>
            <button type="button" style={secondaryButton} onClick={() => setView('Consent')}>Review example access request</button>
          </div>
        </section>
      )}

      {view === 'Records' && (
        <section aria-label="Synthetic records" style={panel}>
          <p style={{ color: '#087a74', fontWeight: 800, margin: '0 0 8px' }}>SYNTHETIC RECORD · NOT A CLINICAL PDF</p>
          <h2 style={{ marginTop: 0 }}>Medical reports</h2>
          <p style={muted}>This sample card illustrates reviewing report metadata. It is not an imported, encrypted or verified medical document.</p>
          <article style={{ padding: 20, background: '#eff7f5', borderRadius: 12 }}>
            <strong>Example blood test summary</strong>
            <p style={muted}>Demo Hospital A · Sample report · No medical results or personal identifiers</p>
            <button type="button" style={secondaryButton} onClick={() => setReportOpen((open) => !open)}>
              {reportOpen ? 'Close example preview' : 'Open example preview'}
            </button>
            {reportOpen && (
              <div role="region" aria-label="Example report preview" style={{ marginTop: 16, padding: 16, background: '#fff', borderRadius: 10 }}>
                <strong>Sample preview — no PDF bytes</strong>
                <p style={muted}>Source: fictional sample. Status: demonstration only. The real app requires a scanned, encrypted original and explicit consent to share it.</p>
              </div>
            )}
          </article>
          <form
            aria-label="Create fictional demo report"
            style={{ borderTop: '1px solid #c8dcd7', marginTop: 24, paddingTop: 20, display: 'grid', gap: 12 }}
            onSubmit={(event) => {
              event.preventDefault()
              const candidate = newRecordLabel.trim().toUpperCase()
              if (!/^DEMO-[A-Z0-9 _-]{3,30}$/.test(candidate)) {
                setRecordError('Use a fictional report label such as DEMO-LAB-PANEL. No real patient information.')
                return
              }
              setSampleRecords((records) => [...records, candidate])
              setNewRecordLabel('')
              setRecordError(null)
            }}
          >
            <label htmlFor="demo-report-label" style={{ fontWeight: 700 }}>Add an example report</label>
            <input
              id="demo-report-label"
              placeholder="DEMO-LAB-PANEL"
              value={newRecordLabel}
              maxLength={35}
              onChange={(event) => {
                setNewRecordLabel(event.target.value.toUpperCase())
                setRecordError(null)
              }}
              style={{ padding: '12px 14px', border: '1px solid #91b4b0', borderRadius: 10, color: '#153b3a', fontSize: 16 }}
            />
            <p style={{ ...muted, margin: 0 }}>Only invented DEMO-* labels are accepted; nothing is uploaded or saved.</p>
            {recordError && <p role="alert" style={{ color: '#aa1b31', margin: 0 }}>{recordError}</p>}
            <button type="submit" style={buttonStyle}>Add fictional report</button>
          </form>
          <ul aria-label="Fictional report list" style={{ lineHeight: 1.8, marginTop: 16 }}>
            {sampleRecords.map((item, index) => <li key={index}>{item} — sample only</li>)}
          </ul>
          <button type="button" style={{ ...buttonStyle, marginTop: 22 }} onClick={() => setView('Timeline')}>Continue to timeline</button>
        </section>
      )}

      {view === 'Timeline' && (
        <section aria-label="Synthetic timeline" style={panel}>
          <h2 style={{ marginTop: 0 }}>Health timeline</h2>
          <p style={muted}>Illustrative sequence only; no database events were read or generated.</p>
          <ol style={{ paddingLeft: 25, lineHeight: 2 }}>
            <li><strong>Day 1:</strong> Example document received</li>
            <li><strong>Day 2:</strong> Sample report reviewed</li>
            <li><strong>Day 3:</strong> Example clinician access request arrives</li>
            {sampleRecords.slice(1).map((item, index) => (
              <li key={index}><strong>Demo event {index + 4}:</strong> Added {item} (local simulation)</li>
            ))}
          </ol>
          <button type="button" style={buttonStyle} onClick={() => setView('Consent')}>Continue to access control</button>
        </section>
      )}

      {view === 'Consent' && (
        <section aria-label="Synthetic consent simulation" style={panel}>
          <p style={{ color: '#087a74', fontWeight: 800, margin: '0 0 8px' }}>LOCAL UI SIMULATION — NOT A SIGNED CONSENT</p>
          <h2 style={{ marginTop: 0 }}>Decide who can access your records</h2>
          <p style={muted}>Fictional Dr. Example at Demo Hospital B requests access to a sample report for a clinical review. No real provider, patient, device signature or medical record is involved.</p>
          <div role="status" style={{ padding: 18, background: '#eff7f5', borderRadius: 12, margin: '18px 0' }}>
            <strong>Example request status: {decision.toUpperCase()}</strong>
            <p style={{ ...muted, marginBottom: 0 }}>
              {decision === 'pending' && 'In the real app, this request requires an independently signed patient approval before any original document can be opened.'}
              {decision === 'approved' && 'Simulated approval on this screen only. No capability, signed approval, or document access was issued.'}
              {decision === 'denied' && 'Simulated denial. No real clinician was notified.'}
              {decision === 'revoked' && 'Simulated revocation. No actual clinical authorization was created or revoked.'}
            </p>
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
            {decision === 'pending' && (
              <>
                <button type="button" style={buttonStyle} onClick={() => setDecision('approved')}>Simulate approval</button>
                <button type="button" style={secondaryButton} onClick={() => setDecision('denied')}>Simulate denial</button>
              </>
            )}
            {decision === 'approved' && (
              <button type="button" style={secondaryButton} onClick={() => setDecision('revoked')}>Simulate revocation</button>
            )}
            {decision !== 'pending' && (
              <button type="button" style={secondaryButton} onClick={() => setDecision('pending')}>Reset simulation</button>
            )}
          </div>
          <p style={{ ...muted, fontSize: 13, marginTop: 22 }}>Actual document access, replay protection, expiry, signed consent and audit remain unavailable until the authenticated staging backend is qualified.</p>
        </section>
      )}

      <footer style={{ marginTop: 30, ...muted, fontSize: 14 }}>
        This demo never creates accounts, stores data, sends SMS or bypasses the real patient sign-in.
      </footer>
    </main>
  )
}
