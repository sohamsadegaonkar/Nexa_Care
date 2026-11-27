// Server-side infrastructure probe. Sends no user request data or credentials.
export async function azureBackendReady() {
  const api = process.env.NEXA_AZURE_STAGING_EXPECTED_API_ORIGIN
  const web = process.env.NEXA_AZURE_STAGING_EXPECTED_WEB_ORIGIN
  const environment = api?.match(
    /^https:\/\/nexa-stg-api\.internal\.([a-z0-9-]+\.malaysiawest\.azurecontainerapps\.io)$/
  )?.[1]
  if (
    !environment ||
    api !== process.env.API_PROXY_TARGET ||
    web !== `https://nexa-stg-web.${environment}` ||
    web !== process.env.NEXT_PUBLIC_API_URL
  ) {
    return false
  }

  try {
    const response = await fetch(`${api}/health`, {
      cache: 'no-store',
      redirect: 'error',
      signal: AbortSignal.timeout(4000),
    })
    if (!response.ok) return false
    const payload = await response.json()
    return (
      payload?.status === 'ok' &&
      ['postgres', 'redis', 'workers', 'audit_outbox', 'patient_source_scanner'].every(
        (dependency) => payload.checks?.[dependency] === 'ok'
      )
    )
  } catch {
    return false
  }
}
