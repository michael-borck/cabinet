/// <reference types="vite/client" />

interface UpdateInfo {
  version: string
  releaseDate: string
  releaseName?: string | null
}

interface UpdateProgress {
  bytesPerSecond: number
  percent: number
  transferred: number
  total: number
}

declare global {
  interface Window {
    electron: {
      ipcRenderer: {
        invoke(channel: string, ...args: unknown[]): Promise<unknown>
      }
    }
    api: {
      selectFolder: () => Promise<string | null>
      revealInFinder: (path: string) => Promise<void>
      openFile: (path: string) => Promise<void>

      // Auto-updater
      checkForUpdates: () => Promise<void>
      downloadUpdate: () => Promise<void>
      installUpdate: () => Promise<void>
      onUpdateAvailable: (cb: (info: UpdateInfo) => void) => () => void
      onUpdateNotAvailable: (cb: (info: UpdateInfo) => void) => () => void
      onUpdateDownloadProgress: (cb: (progress: UpdateProgress) => void) => () => void
      onUpdateDownloaded: (cb: (info: UpdateInfo) => void) => () => void
      onUpdateError: (cb: (error: string) => void) => () => void
    }
  }
}

export {}
