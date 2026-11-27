import { NextResponse } from 'next/server'

export const dynamic = 'force-dynamic'

// Liveness is intentionally not a readiness/release approval.
export function GET() {
  if (process.env.NEXA_AZURE_STAGING_RUNTIME === '1') {
    return NextResponse.json(
      { status: 'alive', environment: 'staging', component: 'web-ui' },
      { headers: { 'Cache-Control': 'no-store' } }
    )
  }
  if (process.env.NEXA_STAGING_WEB_PREVIEW !== '1') {
    return NextResponse.json({ error_code: 'NOT_FOUND' }, { status: 404 })
  }
  return NextResponse.json(
    { status: 'alive', environment: 'staging', component: 'web-ui', readiness: 'blocked' },
    { status: 200, headers: { 'Cache-Control': 'no-store' } }
  )
}
