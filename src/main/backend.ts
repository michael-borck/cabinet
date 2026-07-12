import { ChildProcess, spawn } from 'child_process'
import { randomBytes } from 'crypto'
import { join } from 'path'
import { app } from 'electron'
import { is } from '@electron-toolkit/utils'

let backendProcess: ChildProcess | null = null
const BACKEND_PORT = 8420

// Per-launch shared secret: the backend rejects API requests without it, so
// other local processes can't read settings (LLM key) or register sources.
const API_TOKEN = randomBytes(32).toString('hex')

export async function startBackend(): Promise<void> {
  // Idempotent: ignore double-starts while a backend is already running
  if (backendProcess) return

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
        String(BACKEND_PORT),
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
    // Production: use bundled PyInstaller executable
    const ext = process.platform === 'win32' ? '.exe' : ''
    const backendPath = join(process.resourcesPath, 'backend', `cabinet-api${ext}`)

    backendProcess = spawn(backendPath, ['--port', String(BACKEND_PORT), '--host', '127.0.0.1'], {
      stdio: ['pipe', 'pipe', 'pipe'],
      env: { ...process.env, CABINET_API_TOKEN: API_TOKEN }
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
      const response = await fetch(`http://127.0.0.1:${BACKEND_PORT}/api/v1/health`)
      if (response.ok) return
    } catch {
      // Backend not ready yet
    }
    await new Promise((resolve) => setTimeout(resolve, delay))
  }
  console.warn('Backend did not start in time, continuing anyway')
}

export function getBackendPort(): number {
  return BACKEND_PORT
}

export function getApiToken(): string {
  return API_TOKEN
}
