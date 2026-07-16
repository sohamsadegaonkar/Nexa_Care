import { spawn, spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const BUILD_TIMEOUT_MS = 240_000
const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const nextAppRoot = path.join(repoRoot, 'apps', 'next')
const yarnPath = path.join(repoRoot, '.yarn', 'releases', 'yarn-4.5.0.cjs')

const buildEnvironment = {
  ...process.env,
  NEXT_PUBLIC_API_URL: 'https://api.example.test',
  NEXT_PUBLIC_DEMO_MODE: 'false',
}

function terminateProcessTree(child) {
  if (!child.pid) return

  if (process.platform === 'win32') {
    spawnSync('taskkill', ['/pid', String(child.pid), '/t', '/f'], {
      stdio: 'ignore',
      windowsHide: true,
    })
    return
  }

  try {
    process.kill(-child.pid, 'SIGTERM')
  } catch {
    child.kill('SIGTERM')
  }
}

const startedAt = Date.now()
console.log(`Starting Next production build verification (timeout: ${BUILD_TIMEOUT_MS / 1000}s).`)

const child = spawn(process.execPath, [yarnPath, 'build'], {
  cwd: nextAppRoot,
  env: buildEnvironment,
  detached: process.platform !== 'win32',
  stdio: ['ignore', 'pipe', 'pipe'],
  windowsHide: true,
})

child.stdout.pipe(process.stdout)
child.stderr.pipe(process.stderr)

let timedOut = false
const timeout = setTimeout(() => {
  timedOut = true
  console.error(`Next production build exceeded ${BUILD_TIMEOUT_MS / 1000}s; terminating its process tree.`)
  terminateProcessTree(child)
}, BUILD_TIMEOUT_MS)

child.once('error', (error) => {
  clearTimeout(timeout)
  console.error(`Unable to start the Next production build: ${error.message}`)
  process.exitCode = 1
})

child.once('close', (code, signal) => {
  clearTimeout(timeout)
  const durationSeconds = ((Date.now() - startedAt) / 1000).toFixed(1)

  if (timedOut) {
    console.error(`Next production build verification failed after ${durationSeconds}s (timeout).`)
    process.exitCode = 124
    return
  }

  if (code !== 0) {
    console.error(
      `Next production build verification failed after ${durationSeconds}s ` +
        `(exit code: ${code ?? 'none'}, signal: ${signal ?? 'none'}).`
    )
    process.exitCode = code ?? 1
    return
  }

  console.log(`Next production build verification passed in ${durationSeconds}s.`)
})
