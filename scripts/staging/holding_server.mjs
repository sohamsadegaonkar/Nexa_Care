// Isolated staging holding gateway: deliberately exposes NO patient/auth/application routes.
// A 200 health check means the gateway is alive, NEVER that Nexa Care is release ready.
import { createServer } from 'node:http';

if (process.env.STAGING_MAINTENANCE_ONLY !== '1') {
  process.stderr.write('STAGING_MAINTENANCE_ONLY_REQUIRED\n');
  process.exit(2);
}
const port = Number(process.env.PORT || 8000);
if (!Number.isInteger(port) || port < 1 || port > 65535) {
  process.stderr.write('INVALID_STAGING_PORT\n');
  process.exit(2);
}
const page = `<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Nexa Care — Staging Not Ready</title><style>
:root{color-scheme:dark}body{margin:0;font-family:system-ui,sans-serif;background:#101827;color:#f4f7ff;min-height:100vh;display:grid;place-items:center}
main{max-width:40rem;margin:1.4rem;padding:2.5rem;border:1px solid #3c465b;border-radius:1.2rem;background:#182338}
h1{font-size:2rem;letter-spacing:-.04em}p{line-height:1.65;color:#cbd5e1}.tag{color:#fbbf24;font-size:.8rem;letter-spacing:.11em;font-weight:800}
small{color:#94a3b8}</style></head><body><main><div class="tag">STAGING • NOT READY</div>
<h1>Nexa Care staging is being qualified</h1>
<p>This isolated environment is not open for patient or clinician sign-in. Hosted test OTP, authenticated API, document scanning and device/browser validation are still release blockers.</p>
<p>No clinical records can be accessed from this maintenance gateway.</p>
<small>No production services or patient data are connected.</small></main></body></html>`;

createServer((request, response) => {
  const headers = {
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer',
    'X-Frame-Options': 'DENY',
    'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
  };
  const path = new URL(request.url || '/', 'http://localhost').pathname;
  if (request.method === 'GET' && path === '/healthz') {
    response.writeHead(200, { ...headers, 'Content-Type': 'application/json' });
    response.end(JSON.stringify({ status: 'alive', environment: 'staging', readiness: 'blocked' }));
  } else if (request.method === 'GET' && path === '/readyz') {
    response.writeHead(503, { ...headers, 'Content-Type': 'application/json' });
    response.end(JSON.stringify({ status: 'blocked', reason: 'staging_not_qualified' }));
  } else if (request.method === 'GET' && path === '/') {
    response.writeHead(200, { ...headers, 'Content-Type': 'text/html; charset=utf-8' });
    response.end(page);
  } else {
    response.writeHead(503, { ...headers, 'Content-Type': 'application/json' });
    response.end(JSON.stringify({ error_code: 'STAGING_NOT_READY' }));
  }
}).listen(port, '0.0.0.0');
