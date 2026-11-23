import { ChildProcess, spawn } from 'child_process'
import { randomBytes } from 'crypto'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'fs'
import { createServer } from 'net'
import { join } from 'path'
import { app, safeStorage } from 'electron'
import { is } from '@electron-toolkit/utils'

let backendProcess: ChildProcess | null = null
// Chosen per launch so a fixed port collision doesn't break the app.
let backendPort = 8420

// Per-launch shared secret: the backend rejects API requests without it, so
// other local processes can't read settings (LLM key) or register sources.
const API_TOKEN = randomBytes(32).toString('hex')

async function findFreePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = createServer()
    server.unref()
    server.on('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      const port = address && typeof address === 'object' ? address.port : 0
      server.close(() => resolve(port))
    })
  })
}

/**
 * Load the at-rest encryption key for stored API keys. The key itself is a
 * random 32-byte value protected by the OS keychain via safeStorage; the
 * backend only sees the decrypted key in its environment.
 */
function loadEncryptionKey(): string | undefined {
  try {
    if (!safeStorage.isEncryptionAvailable()) {
      console.warn(
        '[backend] safeStorage unavailable; API keys will be stored unencrypted'
      )
      return undefined
    }
    const userData = app.getPath('userData')
    mkdirSync(userData, { recursive: true })
    const keyPath = join(userData, 'backend-key.bin')
    if (existsSync(keyPath)) {
      return safeStorage.decryptString(readFileSync(keyPath))
    }
    const key = randomBytes(32).toString('hex')
    writeFileSync(keyPath, safeStorage.encryptString(key), { mode: 0o600 })
    return key
  } catch (err) {
    console.error('[backend] Could not load encryption key:', err)
    return undefined
  }
}

export async function startBackend(): Promise<void> {
  // Idempotent: ignore double-starts while a backend is already running
  if (backendProcess) return

  const freePort = await findFreePort()
  backendPort = freePort || 8420

  if (is.dev) {
    // Development: use uv to run from source
    const backendDir = join(app.getAppPath(), 'backend')

    backendProcess = spawn(
      'uv',
      [
        'run',
        'uvicorn',
        'cabinet.app:create_app',
        '--factory',
        '--port',
        String(backendPort),
        '--host',
        '127.0.0.1'
      ],
      {
        cwd: backendDir,
        stdio: ['pipe', 'pipe', 'pipe'],
        env: { ...process.env, CABINET_API_TOKEN: API_TOKEN }
      }
    )
  } else {
    // Production: use bundled PyInstaller executable.
    // The backend's default DB path is relative to its (read-only, ephemeral)
    // bundle, so point it at Electron's userData directory.
    const ext = process.platform === 'win32' ? '.exe' : ''
    const backendPath = join(process.resourcesPath, 'backend', `cabinet-api${ext}`)
    const userData = app.getPath('userData')
    mkdirSync(userData, { recursive: true })
    const env: NodeJS.ProcessEnv = {
      ...process.env,
      CABINET_API_TOKEN: API_TOKEN,
      CABINET_DB_PATH: join(userData, 'cabinet.db'),
      CABINET_CURATION_PATH: join(userData, 'curation.json')
    }
    const encryptionKey = loadEncryptionKey()
    if (encryptionKey) env.CABINET_ENCRYPTION_KEY = encryptionKey

    backendProcess = spawn(backendPath, ['--port', String(backendPort), '--host', '127.0.0.1'], {
      stdio: ['pipe', 'pipe', 'pipe'],
      env
    })
  }

  backendProcess.stdout?.on('data', (data) => {
    console.log(`[backend] ${data}`)
  })

  backendProcess.stderr?.on('data', (data) => {
    console.error(`[backend] ${data}`)
  })

  backendProcess.on('error', (err) => {
    console.error(`[backend] Failed to start: ${err.message}`)
  })

  // Clear the reference if the backend exits on its own (crash, etc.)
  const child = backendProcess
  child.on('exit', () => {
    if (backendProcess === child) {
      backendProcess = null
    }
  })

  // PyInstaller bundles can be slow to start on first run
  const maxRetries = is.dev ? 30 : 120
  await waitForBackend(maxRetries)
}

function hasExited(child: ChildProcess): boolean {
  return child.exitCode !== null || child.signalCode !== null
}

/**
 * Stop the backend gracefully: SIGTERM first (plain kill() on Windows),
 * escalate to SIGKILL if it hasn't exited after 5 seconds.
 * Safe to call multiple times; resolves once the process has exited.
 */
export function stopBackend(): Promise<void> {
  const child = backendProcess
  backendProcess = null

  if (!child || hasExited(child)) {
    return Promise.resolve()
  }

  return new Promise<void>((resolve) => {
    // Unref so this timer can't keep the app alive during quit
    const killTimer = setTimeout(() => {
      if (!hasExited(child)) {
        console.warn('[backend] Did not exit after SIGTERM, sending SIGKILL')
        child.kill('SIGKILL')
      }
    }, 5000)
    killTimer.unref()

    child.once('exit', () => {
      clearTimeout(killTimer)
      resolve()
    })

    if (process.platform === 'win32') {
      // No POSIX signals on Windows; kill() terminates the process
      child.kill()
    } else {
      child.kill('SIGTERM')
    }
  })
}

async function waitForBackend(maxRetries = 30, delay = 1000): Promise<void> {
  for (let i = 0; i < maxRetries; i++) {
    try {
      const response = await fetch(`http://127.0.0.1:${backendPort}/api/v1/health`)
      if (response.ok) return
    } catch {
      // Backend not ready yet
    }
    await new Promise((resolve) => setTimeout(resolve, delay))
  }
  console.warn('Backend did not start in time, continuing anyway')
}

export function getBackendPort(): number {
  return backendPort
}

export function getApiToken(): string {
  return API_TOKEN
}
