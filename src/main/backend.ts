import { ChildProcess, spawn } from 'child_process'
import { join } from 'path'
import { app } from 'electron'
import { is } from '@electron-toolkit/utils'

let backendProcess: ChildProcess | null = null
const BACKEND_PORT = 8420

export async function startBackend(): Promise<void> {
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
        stdio: ['pipe', 'pipe', 'pipe']
      }
    )
  } else {
    // Production: use bundled PyInstaller executable
    const ext = process.platform === 'win32' ? '.exe' : ''
    const backendPath = join(process.resourcesPath, 'backend', `cabinet-api${ext}`)

    backendProcess = spawn(backendPath, ['--port', String(BACKEND_PORT), '--host', '127.0.0.1'], {
      stdio: ['pipe', 'pipe', 'pipe']
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

  // PyInstaller bundles can be slow to start on first run
  const maxRetries = is.dev ? 30 : 120
  await waitForBackend(maxRetries)
}

export function stopBackend(): void {
  if (backendProcess) {
    backendProcess.kill()
    backendProcess = null
  }
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
