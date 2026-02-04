import { create } from 'zustand'
import { persist } from 'zustand/middleware'

interface UIState {
  theme: 'light' | 'dark'
  sidebarWidth: number
  detailsPanelWidth: number
  viewMode: 'list' | 'grid'
  toggleTheme: () => void
  setViewMode: (mode: 'list' | 'grid') => void
  setSidebarWidth: (w: number) => void
  setDetailsPanelWidth: (w: number) => void
}

export const useUIStore = create<UIState>()(
  persist(
    (set) => ({
      theme: 'light',
      sidebarWidth: 240,
      detailsPanelWidth: 300,
      viewMode: 'list',
      toggleTheme: () =>
        set((s) => {
          const next = s.theme === 'light' ? 'dark' : 'light'
          document.documentElement.classList.toggle('dark', next === 'dark')
          return { theme: next }
        }),
      setViewMode: (mode) => set({ viewMode: mode }),
      setSidebarWidth: (w) => set({ sidebarWidth: w }),
      setDetailsPanelWidth: (w) => set({ detailsPanelWidth: w }),
    }),
    { name: 'file-sorter-ui' }
  )
)
