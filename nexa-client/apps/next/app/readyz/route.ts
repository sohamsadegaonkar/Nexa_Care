import { NextResponse } from 'next/server'

export const dynamic = 'force-dynamic'

// Do not report ready until real API, safe OTP and full staging E2E are qualified.
export function GET() {
  if (process.env.NEXA_STAGING_WEB_PREVIEW !== '1') {
    return NextResponse.json({ error_code: 'NOT_FOUND' }, { status: 404 })
  }
  return NextResponse.json(
    { status: 'blocked', error_code: 'STAGING_BACKEND_NOT_CONNECTED' },
    { status: 503, headers: { 'Cache-Control': 'no-store' } }
  )
}
