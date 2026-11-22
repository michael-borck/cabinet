import { create } from 'zustand'
import { persist } from 'zustand/middleware'

interface UIState {
  theme: 'light' | 'dark'
  sidebarWidth: number
  detailsPanelWidth: number
  viewMode: 'list' | 'grid'
  settingsOpen: boolean
  toggleTheme: () => void
  setViewMode: (mode: 'list' | 'grid') => void
  setSidebarWidth: (w: number) => void
  setDetailsPanelWidth: (w: number) => void
  setSettingsOpen: (open: boolean) => void
}

export const useUIStore = create<UIState>()(
  persist(
    (set) => ({
      theme: 'light',
      sidebarWidth: 240,
      detailsPanelWidth: 300,
      viewMode: 'list',
      settingsOpen: false,
      toggleTheme: () =>
        set((s) => {
          const next = s.theme === 'light' ? 'dark' : 'light'
          document.documentElement.classList.toggle('dark', next === 'dark')
          return { theme: next }
        }),
      setViewMode: (mode) => set({ viewMode: mode }),
      setSidebarWidth: (w) => set({ sidebarWidth: w }),
      setDetailsPanelWidth: (w) => set({ detailsPanelWidth: w }),
      setSettingsOpen: (open) => set({ settingsOpen: open }),
    }),
    {
      name: 'file-sorter-ui',
      // Dialog state is session-only; everything else persists.
      partialize: (state) => ({
        theme: state.theme,
        sidebarWidth: state.sidebarWidth,
        detailsPanelWidth: state.detailsPanelWidth,
        viewMode: state.viewMode,
      }),
      onRehydrateStorage: () => (state) => {
        // Apply the persisted theme to the document on startup so the UI
        // doesn't boot light when dark mode was saved.
        document.documentElement.classList.toggle('dark', state?.theme === 'dark')
      },
    }
  )
)
