import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createServer } from 'node:net';

async function freePort() {
  const server = createServer();
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return port;
}

test('staging holding gateway is alive but NOT ready and never serves API/auth', async () => {
  const port = await freePort();
  const child = spawn(process.execPath, ['scripts/staging/holding_server.mjs'], {
    cwd: process.cwd(),
    env: { ...process.env, PORT: String(port), STAGING_MAINTENANCE_ONLY: '1' },
    stdio: 'ignore',
  });
  try {
    let alive;
    for (let attempt = 0; attempt < 40; attempt++) {
      try {
        alive = await fetch(`http://127.0.0.1:${port}/healthz`);
        break;
      } catch {
        await new Promise((r) => setTimeout(r, 75));
      }
    }
    assert.ok(alive, 'holding server did not start');
    assert.equal(alive.status, 200);
    assert.deepEqual(await alive.json(), {status:'alive', environment:'staging', readiness:'blocked'});
    const ready = await fetch(`http://127.0.0.1:${port}/readyz`);
    assert.equal(ready.status, 503);
    assert.equal((await ready.json()).reason, 'staging_not_qualified');
    for (const route of ['/api/v2/auth/otp/send', '/api/v2/consent/request', '/auth/login']) {
      const response = await fetch(`http://127.0.0.1:${port}${route}`);
      assert.equal(response.status, 503, route);
      assert.equal(response.headers.get('cache-control'), 'no-store');
    }
    const page = await fetch(`http://127.0.0.1:${port}/`);
    assert.equal(page.status, 200);
    assert.match(await page.text(), /STAGING • NOT READY/);
    assert.equal(page.headers.get('x-frame-options'), 'DENY');
  } finally {
    child.kill('SIGTERM');
  }
});

test('holding gateway refuses startup without explicit maintenance flag', async () => {
  const port = await freePort();
  const child = spawn(process.execPath, ['scripts/staging/holding_server.mjs'], {
    cwd: process.cwd(),
    env: { ...process.env, PORT: String(port), STAGING_MAINTENANCE_ONLY: '0' },
  });
  const outputs = [];
  child.stderr.on('data', (b) => outputs.push(b.toString()));
  const code = await new Promise((resolve) => child.on('exit', resolve));
  assert.equal(code, 2);
  assert.match(outputs.join(''), /STAGING_MAINTENANCE_ONLY_REQUIRED/);
});
