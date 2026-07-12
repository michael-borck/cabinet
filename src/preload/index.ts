import { contextBridge, ipcRenderer } from 'electron'
import { electronAPI } from '@electron-toolkit/preload'

const api = {
  getApiToken: (): Promise<string> => ipcRenderer.invoke('get-api-token'),
  selectFolder: (): Promise<string | null> => ipcRenderer.invoke('select-folder'),
  revealInFinder: (path: string): Promise<void> => ipcRenderer.invoke('reveal-in-finder', path),
  openFile: (path: string): Promise<void> => ipcRenderer.invoke('open-file', path),

  // Auto-updater
  checkForUpdates: (): Promise<void> => ipcRenderer.invoke('updater:check'),
  downloadUpdate: (): Promise<void> => ipcRenderer.invoke('updater:download'),
  installUpdate: (): Promise<void> => ipcRenderer.invoke('updater:install'),

  onUpdateAvailable: (cb: (...args: unknown[]) => void): (() => void) => {
    const listener = (_event: unknown, ...args: unknown[]): void => cb(...args)
    ipcRenderer.on('updater:update-available', listener)
    return () => ipcRenderer.removeListener('updater:update-available', listener)
  },
  onUpdateNotAvailable: (cb: (...args: unknown[]) => void): (() => void) => {
    const listener = (_event: unknown, ...args: unknown[]): void => cb(...args)
    ipcRenderer.on('updater:update-not-available', listener)
    return () => ipcRenderer.removeListener('updater:update-not-available', listener)
  },
  onUpdateDownloadProgress: (cb: (...args: unknown[]) => void): (() => void) => {
    const listener = (_event: unknown, ...args: unknown[]): void => cb(...args)
    ipcRenderer.on('updater:download-progress', listener)
    return () => ipcRenderer.removeListener('updater:download-progress', listener)
  },
  onUpdateDownloaded: (cb: (...args: unknown[]) => void): (() => void) => {
    const listener = (_event: unknown, ...args: unknown[]): void => cb(...args)
    ipcRenderer.on('updater:update-downloaded', listener)
    return () => ipcRenderer.removeListener('updater:update-downloaded', listener)
  },
  onUpdateError: (cb: (...args: unknown[]) => void): (() => void) => {
    const listener = (_event: unknown, ...args: unknown[]): void => cb(...args)
    ipcRenderer.on('updater:error', listener)
    return () => ipcRenderer.removeListener('updater:error', listener)
  }
}

if (process.contextIsolated) {
  try {
    contextBridge.exposeInMainWorld('electron', electronAPI)
    contextBridge.exposeInMainWorld('api', api)
  } catch (error) {
    console.error(error)
  }
} else {
  // @ts-ignore
  window.electron = electronAPI
  // @ts-ignore
  window.api = api
}
