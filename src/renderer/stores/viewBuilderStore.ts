import { create } from 'zustand'
import type { SavedView } from '@/services/api'

interface ViewBuilderState {
  open: boolean
  editingView: SavedView | null
  openBuilder: (view?: SavedView | null) => void
  closeBuilder: () => void
}

export const useViewBuilderStore = create<ViewBuilderState>()((set) => ({
  open: false,
  editingView: null,
  openBuilder: (view = null) => set({ open: true, editingView: view }),
  closeBuilder: () => set({ open: false, editingView: null }),
}))
