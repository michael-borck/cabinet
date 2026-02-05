import { app, BrowserWindow, shell, ipcMain, dialog } from 'electron'
import { join } from 'path'
import { electronApp, optimizer, is } from '@electron-toolkit/utils'
import { autoUpdater } from 'electron-updater'
import { startBackend, stopBackend } from './backend'

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

app.on('window-all-closed', () => {
  stopBackend()
  if (process.platform !== 'darwin') app.quit()
})

// IPC handlers
ipcMain.handle('select-folder', async () => {
  const result = await dialog.showOpenDialog({
    properties: ['openDirectory']
  })
  return result.canceled ? null : result.filePaths[0]
})

ipcMain.handle('reveal-in-finder', async (_event, filePath: string) => {
  shell.showItemInFolder(filePath)
})

ipcMain.handle('open-file', async (_event, filePath: string) => {
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
