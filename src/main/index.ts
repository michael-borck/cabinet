import { app, BrowserWindow, shell, ipcMain, dialog } from 'electron'
import { existsSync, statSync } from 'fs'
import { isAbsolute, join } from 'path'
import { electronApp, optimizer, is } from '@electron-toolkit/utils'
import { autoUpdater } from 'electron-updater'
import { getApiToken, startBackend, stopBackend } from './backend'

let mainWindow: BrowserWindow | null = null

// Auto-updater configuration
autoUpdater.autoDownload = false
autoUpdater.autoInstallOnAppQuit = true

function setupAutoUpdater(): void {
  autoUpdater.on('update-available', (info) => {
    mainWindow?.webContents.send('updater:update-available', info)
  })

  autoUpdater.on('update-not-available', (info) => {
    mainWindow?.webContents.send('updater:update-not-available', info)
  })

  autoUpdater.on('download-progress', (progress) => {
    mainWindow?.webContents.send('updater:download-progress', progress)
  })

  autoUpdater.on('update-downloaded', (info) => {
    mainWindow?.webContents.send('updater:update-downloaded', info)
  })

  autoUpdater.on('error', (error) => {
    mainWindow?.webContents.send('updater:error', error.message)
  })
}

function isAllowedNavigation(url: string): boolean {
  try {
    const target = new URL(url)
    const devServerUrl = is.dev && process.env['ELECTRON_RENDERER_URL']
    if (devServerUrl) {
      return target.origin === new URL(devServerUrl).origin
    }
    return target.protocol === 'file:'
  } catch {
    return false
  }
}

// Validate renderer-supplied paths before handing them to the shell.
function isValidExistingPath(filePath: unknown): filePath is string {
  return typeof filePath === 'string' && isAbsolute(filePath) && existsSync(filePath)
}

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    show: false,
    titleBarStyle: 'hiddenInset',
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: false
    }
  })

  mainWindow.on('ready-to-show', () => {
    mainWindow?.show()

    // Check for updates 3 seconds after showing the window (skip in dev)
    if (!is.dev) {
      setTimeout(() => {
        autoUpdater.checkForUpdates()
      }, 3000)
    }
  })

  mainWindow.webContents.setWindowOpenHandler((details) => {
    shell.openExternal(details.url)
    return { action: 'deny' }
  })

  // Block navigation away from the app's own origin (dev server in dev,
  // local file in prod) -- the renderer has a privileged preload.
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (!isAllowedNavigation(url)) {
      event.preventDefault()
    }
  })

  if (is.dev && process.env['ELECTRON_RENDERER_URL']) {
    mainWindow.loadURL(process.env['ELECTRON_RENDERER_URL'])
  } else {
    mainWindow.loadFile(join(__dirname, '../renderer/index.html'))
  }
}

app.whenReady().then(async () => {
  electronApp.setAppUserModelId('com.cabinet.desktop')
  app.on('browser-window-created', (_, window) => {
    optimizer.watchWindowShortcuts(window)
  })

  setupAutoUpdater()
  await startBackend()
  createWindow()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

// On macOS the app stays alive with no windows, so keep the backend running
// too -- the 'activate' handler recreates the window and it reconnects.
// On other platforms closing all windows quits the app, which stops the
// backend via the 'will-quit' hook below.
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

// Stop the backend when the app actually quits (all platforms). Defer the
// quit until the backend has exited (SIGTERM, escalating to SIGKILL after 5s).
let backendStoppedForQuit = false
app.on('will-quit', (event) => {
  if (backendStoppedForQuit) return
  backendStoppedForQuit = true
  event.preventDefault()
  stopBackend().finally(() => app.exit(0))
})

// IPC handlers
ipcMain.handle('get-api-token', () => getApiToken())

ipcMain.handle('select-folder', async () => {
  const result = await dialog.showOpenDialog({
    properties: ['openDirectory']
  })
  return result.canceled ? null : result.filePaths[0]
})

ipcMain.handle('reveal-in-finder', async (_event, filePath: string) => {
  if (!isValidExistingPath(filePath)) {
    console.warn('[ipc] reveal-in-finder rejected invalid path:', filePath)
    return
  }
  shell.showItemInFolder(filePath)
})

ipcMain.handle('open-file', async (_event, filePath: string) => {
  if (!isValidExistingPath(filePath)) {
    console.warn('[ipc] open-file rejected invalid path:', filePath)
    return
  }
  const stats = statSync(filePath, { throwIfNoEntry: false })
  if (!stats?.isFile()) {
    console.warn('[ipc] open-file rejected non-file path:', filePath)
    return
  }
  shell.openPath(filePath)
})

// Updater IPC handlers
ipcMain.handle('updater:check', () => {
  autoUpdater.checkForUpdates()
})

ipcMain.handle('updater:download', () => {
  autoUpdater.downloadUpdate()
})

ipcMain.handle('updater:install', () => {
  autoUpdater.quitAndInstall()
})
