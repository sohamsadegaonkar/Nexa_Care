import { NextResponse } from 'next/server'
import { azureBackendReady } from '../../server/azure-backend-readiness'

export const dynamic = 'force-dynamic'

const noStore = { 'Cache-Control': 'no-store' }

// This endpoint is an infrastructure probe. Release qualification is recorded separately.
export async function GET() {
  if (process.env.NEXA_AZURE_STAGING_RUNTIME === '1') {
    const ready = await azureBackendReady()
    return NextResponse.json(
      { status: ready ? 'ready' : 'blocked', scope: 'dependencies' },
      { status: ready ? 200 : 503, headers: noStore }
    )
  }
  if (process.env.NEXA_STAGING_WEB_PREVIEW !== '1') {
    return NextResponse.json({ error_code: 'NOT_FOUND' }, { status: 404 })
  }
  return NextResponse.json(
    { status: 'blocked', error_code: 'STAGING_BACKEND_NOT_CONNECTED' },
    { status: 503, headers: noStore }
  )
}
