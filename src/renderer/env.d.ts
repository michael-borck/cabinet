/// <reference types="vite/client" />

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
    }
  }
}

export {}
